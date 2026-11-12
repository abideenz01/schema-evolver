import json

import pytest

from schema_evolver.models import Contract, RenameProposal, SchemaError  # noqa: F401
from schema_evolver.planner import (apply_writer_mapping, build_plan, contract_phase_blockers,
                                    reader_compatibility)
from schema_evolver.policy import evaluate


@pytest.fixture
def plan(demo):
    contract, ref, batch, changes = demo
    props = [RenameProposal("cust_id", "customer_id", 0.93, "", "llm")]
    return build_plan(contract, evaluate(contract, changes, props, batch, ref))


def test_phase_order_and_deferral(plan):
    order = ["preflight", "expand", "backfill", "switch", "contract"]
    phases = [s.phase for s in plan.steps]
    assert phases == sorted(phases, key=order.index)
    assert all(s.deferred for s in plan.steps if s.phase == "contract")
    assert not any(s.deferred for s in plan.steps if s.phase != "contract")


def test_backfill_is_idempotent_sql(plan):
    s = next(s for s in plan.steps if s.phase == "backfill")
    assert "WHERE `customer_id` IS NULL AND `cust_id` IS NOT NULL" in s.sql


def test_zero_downtime_for_existing_readers(plan, ex):
    consumers = json.loads((ex / "consumers.json").read_text())
    assert reader_compatibility(plan.view_columns, consumers) == {}
    assert set(contract_phase_blockers(plan.next_contract["aliases"], consumers)) == {
        "finance_dashboard", "churn_features"}


def test_reader_breakage_detected():
    assert reader_compatibility(["a"], {"job": ["a", "b"]}) == {"job": ["b"]}


def test_next_contract(plan):
    nc = plan.next_contract
    names = [c["name"] for c in nc["columns"]]
    assert nc["version"] == 2 and "customer_id" in names and "cust_id" not in names
    assert nc["aliases"] == {"cust_id": "customer_id"}
    types = {c["name"]: c["type"] for c in nc["columns"]}
    assert types["loyalty_points"] == "long" and types["amount"] == "double"
    assert "customer_email" not in names  # rescued, not promoted


def test_plan_id_deterministic(demo):
    contract, ref, batch, changes = demo
    props = [RenameProposal("cust_id", "customer_id", 0.93, "", "llm")]
    a = build_plan(contract, evaluate(contract, changes, props, batch, ref))
    b = build_plan(contract, evaluate(contract, changes, props, batch, ref))
    assert a.plan_id == b.plan_id and a.to_sql() == b.to_sql()


def test_writer_mapping_dual_write_and_rescue(plan):
    rec = {"order_id": "O1", "customer_id": "C1", "customer_email": "c1@example.com"}
    out = apply_writer_mapping(rec, plan.writer_mapping)
    assert out["cust_id"] == "C1" and out["customer_id"] == "C1"
    assert "customer_email" not in out
    assert json.loads(out["_rescued_data"]) == {"customer_email": "c1@example.com"}
    assert rec["customer_email"] == "c1@example.com"  # input not mutated


def test_quarantined_plan_has_no_steps(ex):
    from schema_evolver.drift import detect_drift
    from schema_evolver.profiler import profile_records, read_jsonl
    c = Contract.load(ex / "contract_v1.json")
    b = profile_records(read_jsonl(ex / "batch_wrong_source.jsonl"))
    p = build_plan(c, evaluate(c, detect_drift(c, b), [], b, {}))
    assert p.steps == [] and p.to_version == 1


def test_invalid_table_identifier(demo):
    contract, ref, batch, changes = demo
    with pytest.raises(SchemaError):
        build_plan(contract, evaluate(contract, changes, [], batch, ref), "t; DROP TABLE x")
