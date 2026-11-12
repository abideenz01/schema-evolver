from schema_evolver.drift import detect_drift
from schema_evolver.models import Action, Column, Contract, RenameProposal
from schema_evolver.policy import PolicyConfig, evaluate
from schema_evolver.profiler import profile_records


def by_col(report):
    return {d.column: d for d in report.decisions}


def test_demo_decisions(demo):
    contract, ref, batch, changes = demo
    props = [RenameProposal("cust_id", "customer_id", 0.93, "", "llm"),
             RenameProposal("region", "promo_code", 0.81, "", "llm")]
    r = evaluate(contract, changes, props, batch, ref)
    d = by_col(r)
    assert r.verdict == "PROCEED"
    assert d["cust_id"].action == Action.EXPAND_CONTRACT and d["cust_id"].new_column == "customer_id"
    assert d["region"].action == Action.FILL_NULL          # hallucinated rename rejected
    assert d["promo_code"].action == Action.AUTO_APPLY
    assert d["customer_email"].action == Action.RESCUE
    assert d["loyalty_points"].action == Action.AUTO_APPLY
    assert any(x["old"] == "region" and x["new"] == "promo_code" for x in r.rejected_proposals)


def test_high_confidence_alone_is_not_enough(demo):
    contract, ref, batch, changes = demo
    r = evaluate(contract, changes, [RenameProposal("region", "promo_code", 1.0, "", "llm")], batch, ref)
    assert by_col(r)["region"].action == Action.FILL_NULL


def _rename_setup(required=True):
    c = Contract("t", 1, [Column("id", "string", nullable=not required), Column("v", "int")])
    ref = profile_records([{"id": f"K{i}", "v": i} for i in range(50)])
    batch = profile_records([{"key": f"K{i}", "key2": f"K{i}", "v": i} for i in range(50)])
    return c, ref, batch, detect_drift(c, batch)


def test_ambiguous_mapping_rejected():
    c, ref, batch, ch = _rename_setup()
    props = [RenameProposal("id", "key", 0.9, "", "llm"), RenameProposal("id", "key2", 0.9, "", "llm")]
    r = evaluate(c, ch, props, batch, ref)
    assert r.verdict == "QUARANTINE_BATCH"
    assert by_col(r)["id"].action == Action.BLOCK
    assert sum(x["reason"].startswith("ambiguous") for x in r.rejected_proposals) == 2


def test_required_missing_without_rename_blocks():
    c, ref, batch, ch = _rename_setup()
    r = evaluate(c, ch, [], batch, ref)
    assert by_col(r)["id"].action == Action.BLOCK


def test_proposal_without_reference_profile_rejected():
    c, _, batch, ch = _rename_setup()
    r = evaluate(c, ch, [RenameProposal("id", "key", 0.9, "", "llm")], batch, {})
    assert r.rejected_proposals[0]["reason"] == "no reference profile for old column"


def test_proposal_for_non_candidates_rejected():
    c, ref, batch, ch = _rename_setup()
    r = evaluate(c, ch, [RenameProposal("v", "key", 0.9, "", "llm")], batch, ref)
    assert r.rejected_proposals[0]["reason"] == "not a missing/new pair"


def test_wrong_source_guardrail(ex):
    from schema_evolver.profiler import read_jsonl
    c = Contract.load(ex / "contract_v1.json")
    b = profile_records(read_jsonl(ex / "batch_wrong_source.jsonl"))
    r = evaluate(c, detect_drift(c, b), [], b, {})
    assert r.verdict == "QUARANTINE_BATCH" and r.decisions[0].change_kind == "GUARDRAIL"


def test_int_to_double_manual_by_default():
    c = Contract("t", 1, [Column("x", "int")])
    b = profile_records([{"x": 1.5}])
    ch = detect_drift(c, b)
    assert evaluate(c, ch, [], b, {}).verdict == "QUARANTINE_BATCH"
    ok = evaluate(c, ch, [], b, {}, PolicyConfig(allow_int_to_fractional=True))
    assert ok.verdict == "PROCEED"


def test_incompatible_type_blocks():
    c = Contract("t", 1, [Column("x", "date")])
    b = profile_records([{"x": "not a date"}])
    assert evaluate(c, detect_drift(c, b), [], b, {}).decisions[0].action == Action.BLOCK


def test_auto_change_budget():
    c = Contract("t", 1, [Column("id", "string")])
    b = profile_records([{"id": "a", **{f"c{i}": i for i in range(5)}}])
    r = evaluate(c, detect_drift(c, b), [], b, {}, PolicyConfig(max_auto_changes=3))
    assert r.verdict == "QUARANTINE_BATCH" and r.decisions[-1].change_kind == "GUARDRAIL"


def test_policy_from_dict_ignores_unknown():
    cfg = PolicyConfig.from_dict({"max_auto_changes": 2, "pii_patterns": ["x"], "nope": 1})
    assert cfg.max_auto_changes == 2 and cfg.pii_patterns == ("x",)
