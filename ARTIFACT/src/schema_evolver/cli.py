"""Command line entry point.

  python -m schema_evolver plan  ...   detect drift, propose, gate, write plan
  python -m schema_evolver apply ...   run a plan (dry run unless --spark)
  python -m schema_evolver eval  ...   score rename policies on labeled cases
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .drift import detect_drift
from .evaluate import evaluate_cases, load_cases
from .models import Contract, SchemaError
from .planner import Plan, Step, build_plan, contract_phase_blockers, reader_compatibility
from .policy import PolicyConfig, evaluate
from .profiler import profile_records, read_jsonl
from .proposers import HeuristicProposer, LLMProposer, scripted_llm
from .runner import DryRunExecutor, rollback_statements, run_plan

log = logging.getLogger("schema_evolver")


def cmd_plan(a: argparse.Namespace) -> int:
    contract = Contract.load(a.contract)
    cfg = PolicyConfig.from_dict(json.loads(Path(a.policy).read_text())) if a.policy else PolicyConfig()
    reference = profile_records(read_jsonl(a.reference))
    batch = profile_records(read_jsonl(a.batch))
    changes = detect_drift(contract, batch, cfg.runtime)

    proposals = HeuristicProposer().propose(contract, changes, batch)
    llm_meta = None
    if a.llm_response:
        llm = LLMProposer(scripted_llm(Path(a.llm_response).read_text()))
        proposals += llm.propose(contract, changes, batch)
        llm_meta = {"schema_rejected": llm.rejected, "errors": llm.errors}

    report = evaluate(contract, changes, proposals, batch, reference, cfg)
    plan = build_plan(contract, report, a.table)
    consumers = json.loads(Path(a.consumers).read_text()) if a.consumers else {}
    broken = reader_compatibility(plan.view_columns, consumers) if plan.steps else {}

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    result = {"plan_id": plan.plan_id, **report.to_dict(),
              "changes": [c.__dict__ | {"kind": c.kind.value} for c in changes],
              "llm": llm_meta, "reader_breakage": broken,
              "contract_phase_blocked_by": contract_phase_blockers(plan.next_contract["aliases"], consumers)}
    (out / "decision_report.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2))
    (out / "plan.sql").write_text(plan.to_sql())
    (out / f"contract_v{plan.to_version}.json").write_text(json.dumps(plan.next_contract, indent=2))
    log.info(json.dumps({"event": "plan_written", "plan_id": plan.plan_id, "verdict": report.verdict,
                         "counts": report.counts(), "rejected_proposals": len(report.rejected_proposals)}))
    print(json.dumps({"plan_id": plan.plan_id, "verdict": report.verdict, "counts": report.counts(),
                      "reader_breakage": broken}, indent=2))
    if broken:
        return 3
    return 0 if report.verdict == "PROCEED" else 2


def _load_plan(path: str) -> Plan:
    d = json.loads(Path(path).read_text())
    d["steps"] = [Step(**s) for s in d["steps"]]
    return Plan(**d)


def cmd_apply(a: argparse.Namespace) -> int:
    plan = _load_plan(a.plan)
    ex = DryRunExecutor()
    journal = run_plan(plan, ex, a.journal, include_deferred=a.include_deferred)
    print(json.dumps({"executed": ex.statements, "done": journal["done"],
                      "rollback": rollback_statements(plan, journal)}, indent=2))
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    res = evaluate_cases(load_cases(a.cases), a.threshold)
    print(json.dumps({k: v for k, v in res.items() if k != "cases"} if not a.verbose else res, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    p = argparse.ArgumentParser(prog="schema_evolver")
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan")
    pp.add_argument("--contract", required=True)
    pp.add_argument("--reference", required=True, help="JSONL sample the contract was built from")
    pp.add_argument("--batch", required=True, help="new JSONL batch")
    pp.add_argument("--table")
    pp.add_argument("--consumers")
    pp.add_argument("--policy")
    pp.add_argument("--llm-response", help="file with a model response (scripted demo)")
    pp.add_argument("--out", default="out")
    pa = sub.add_parser("apply")
    pa.add_argument("--plan", required=True)
    pa.add_argument("--journal", required=True)
    pa.add_argument("--include-deferred", action="store_true")
    pe = sub.add_parser("eval")
    pe.add_argument("--cases", required=True)
    pe.add_argument("--threshold", type=float, default=0.7)
    pe.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    try:
        return {"plan": cmd_plan, "apply": cmd_apply, "eval": cmd_eval}[a.cmd](a)
    except (SchemaError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
