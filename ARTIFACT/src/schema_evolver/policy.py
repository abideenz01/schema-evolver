"""The deterministic policy gate. This is where autonomy is bounded.

A rename proposal (from any proposer, including an LLM) is accepted only when
data evidence supports it. Model confidence is recorded but never sufficient.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .drift import writable_into
from .models import (Action, Change, ChangeKind, Contract, Decision, RenameProposal,
                     widening_kind)
from .profiler import ColumnProfile

DEFAULT_PII = (r"email", r"phone", r"mobile", r"ssn", r"passport", r"iban",
               r"card_?num", r"dob", r"birth", r"national_?id", r"emirates_?id", r"cnic")


@dataclass
class PolicyConfig:
    runtime: str = "delta_4_0"
    min_value_containment: float = 0.5
    min_shape_match: float = 0.95
    min_containment_with_shape: float = 0.1
    min_numeric_range_overlap: float = 0.6
    max_mean_rel_diff: float = 0.25
    max_null_rate_delta: float = 0.2
    allow_int_to_fractional: bool = False
    pii_patterns: tuple[str, ...] = DEFAULT_PII
    max_missing_fraction: float = 0.5
    max_auto_changes: int = 10

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "pii_patterns" in known:
            known["pii_patterns"] = tuple(known["pii_patterns"])
        return cls(**known)


@dataclass
class GateReport:
    verdict: str
    decisions: list[Decision]
    rejected_proposals: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.decisions:
            out[d.action.value] = out.get(d.action.value, 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "counts": self.counts(),
                "decisions": [d.to_dict() for d in self.decisions],
                "rejected_proposals": self.rejected_proposals}


def _range_overlap(a: ColumnProfile, b: ColumnProfile) -> float | None:
    if a.num_min is None or b.num_min is None:
        return None
    lo, hi = max(a.num_min, b.num_min), min(a.num_max, b.num_max)
    union = max(a.num_max, b.num_max) - min(a.num_min, b.num_min)
    if union == 0:
        return 1.0 if lo <= hi else 0.0
    return max(0.0, hi - lo) / union


def shape_match(old: ColumnProfile, new: ColumnProfile) -> float | None:
    """Share of new non-numeric values whose format signature exists in the old column."""
    total = sum(new.shapes.values())
    if not total or not old.shapes:
        return None
    return sum(n for s, n in new.shapes.items() if s in old.shapes) / total


def rename_evidence(old_type: str, old_ref: ColumnProfile | None, new: ColumnProfile,
                    cfg: PolicyConfig) -> dict[str, Any]:
    ev: dict[str, Any] = {"verified": False}
    if old_ref is None:
        ev["failed"] = "no reference profile for old column"
        return ev
    type_ok = writable_into(new, old_type) or widening_kind(old_type, new.type, cfg.runtime) is not None
    ev["type_compatible"] = type_ok
    contain = (len(new.distinct_fps & old_ref.distinct_fps) / len(new.distinct_fps)
               if new.distinct_fps else 0.0)
    ev["value_containment"] = round(contain, 3)
    rng = _range_overlap(old_ref, new)
    ev["numeric_range_overlap"] = None if rng is None else round(rng, 3)
    mean_ok = False
    if old_ref.mean is not None and new.mean is not None:
        denom = max(abs(old_ref.mean), 1e-9)
        rel = abs(new.mean - old_ref.mean) / denom
        ev["mean_rel_diff"] = round(rel, 3)
        mean_ok = rel <= cfg.max_mean_rel_diff
    sm = shape_match(old_ref, new)
    ev["shape_match"] = None if sm is None else round(sm, 3)
    null_delta = abs(new.null_rate - old_ref.null_rate)
    ev["null_rate_delta"] = round(null_delta, 3)
    values_ok = contain >= cfg.min_value_containment or (
        sm is not None and sm >= cfg.min_shape_match and contain >= cfg.min_containment_with_shape) or (
        rng is not None and rng >= cfg.min_numeric_range_overlap and mean_ok)
    if not type_ok:
        ev["failed"] = "type incompatible"
    elif not values_ok:
        ev["failed"] = "value distribution does not match"
    elif null_delta > cfg.max_null_rate_delta:
        ev["failed"] = "null rate shifted"
    else:
        ev["verified"] = True
    return ev


def evaluate(contract: Contract, changes: list[Change], proposals: list[RenameProposal],
             batch: dict[str, ColumnProfile], reference: dict[str, ColumnProfile],
             cfg: PolicyConfig | None = None) -> GateReport:
    cfg = cfg or PolicyConfig()
    cols = contract.by_name()
    decisions: list[Decision] = []
    rejected: list[dict[str, Any]] = []
    missing = {c.column for c in changes if c.kind == ChangeKind.MISSING_COLUMN}
    new = {c.column for c in changes if c.kind == ChangeKind.NEW_COLUMN}

    # Guardrail: a batch that lost most of its contract is a wrong source, not drift.
    if cols and len(missing) / len(cols) > cfg.max_missing_fraction:
        d = Decision(Action.BLOCK, "*", "more than "
                     f"{cfg.max_missing_fraction:.0%} of contract columns missing, suspected wrong source",
                     "GUARDRAIL", evidence={"missing": sorted(missing)})
        return GateReport("QUARANTINE_BATCH", [d], [])

    # Merge proposals from all sources by (old, new).
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for p in proposals:
        if p.old not in missing or p.new not in new:
            rejected.append({"old": p.old, "new": p.new, "source": p.source,
                             "reason": "not a missing/new pair"})
            continue
        m = merged.setdefault((p.old, p.new), {"sources": {}, "rationale": []})
        m["sources"][p.source] = round(p.confidence, 3)
        m["rationale"].append(p.rationale)

    verified: dict[tuple[str, str], dict[str, Any]] = {}
    for (old, nw), meta in sorted(merged.items()):
        ev = rename_evidence(cols[old].type, reference.get(old), batch[nw], cfg)
        ev["proposed_by"] = meta["sources"]
        if ev["verified"]:
            verified[(old, nw)] = ev
        else:
            rejected.append({"old": old, "new": nw, "source": ",".join(meta["sources"]),
                             "reason": ev.get("failed"), "evidence": ev})

    # One-to-one: ambiguous verified mappings are rejected, not guessed.
    accepted: dict[str, tuple[str, dict]] = {}
    olds = [k[0] for k in verified]
    news = [k[1] for k in verified]
    for (old, nw), ev in verified.items():
        if olds.count(old) > 1 or news.count(nw) > 1:
            rejected.append({"old": old, "new": nw, "source": ",".join(ev["proposed_by"]),
                             "reason": "ambiguous mapping, more than one verified candidate"})
        else:
            accepted[old] = (nw, ev)

    consumed_new = {nw for nw, _ in accepted.values()}
    for old, (nw, ev) in sorted(accepted.items()):
        nt = batch[nw].type
        target_type = cols[old].type if writable_into(batch[nw], cols[old].type) else nt
        decisions.append(Decision(Action.EXPAND_CONTRACT, old, "rename verified by data evidence",
                                  "RENAME", new_column=nw, old_type=cols[old].type,
                                  new_type=target_type, evidence=ev))

    for c in changes:
        if c.kind == ChangeKind.NEW_COLUMN and c.column not in consumed_new:
            if any(re.search(p, c.column, re.I) for p in cfg.pii_patterns):
                decisions.append(Decision(Action.RESCUE, c.column,
                                          "name matches a PII pattern, needs classification before landing",
                                          c.kind.value, new_type=c.new_type))
            else:
                decisions.append(Decision(Action.AUTO_APPLY, c.column, "additive nullable column",
                                          c.kind.value, new_type=c.new_type))
        elif c.kind == ChangeKind.MISSING_COLUMN and c.column not in accepted:
            if cols[c.column].nullable:
                decisions.append(Decision(Action.FILL_NULL, c.column,
                                          "nullable column not sent, keep it and land nulls",
                                          c.kind.value, old_type=c.old_type))
            else:
                decisions.append(Decision(Action.BLOCK, c.column,
                                          "required column missing and no verified rename",
                                          c.kind.value, old_type=c.old_type))
        elif c.kind == ChangeKind.TYPE_WIDEN:
            if c.detail == "int_to_fractional" and not cfg.allow_int_to_fractional:
                decisions.append(Decision(Action.BLOCK, c.column,
                                          "integer to fractional widening changes semantics, manual only",
                                          c.kind.value, old_type=c.old_type, new_type=c.new_type))
            else:
                decisions.append(Decision(Action.AUTO_APPLY, c.column,
                                          f"supported {c.detail} widening on {cfg.runtime}",
                                          c.kind.value, old_type=c.old_type, new_type=c.new_type))
        elif c.kind == ChangeKind.TYPE_INCOMPATIBLE:
            decisions.append(Decision(Action.BLOCK, c.column, "type change is not a supported widening",
                                      c.kind.value, old_type=c.old_type, new_type=c.new_type))
        elif c.kind == ChangeKind.NULLS_IN_REQUIRED:
            decisions.append(Decision(Action.BLOCK, c.column, f"nulls in required column ({c.detail})",
                                      c.kind.value, old_type=c.old_type))

    auto = [d for d in decisions if d.action in (Action.AUTO_APPLY, Action.EXPAND_CONTRACT)]
    if len(auto) > cfg.max_auto_changes:
        decisions.append(Decision(Action.BLOCK, "*",
                                  f"{len(auto)} automatic changes exceed budget {cfg.max_auto_changes}",
                                  "GUARDRAIL"))
    verdict = "QUARANTINE_BATCH" if any(d.action == Action.BLOCK for d in decisions) else "PROCEED"
    return GateReport(verdict, decisions, rejected)
