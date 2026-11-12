from schema_evolver.drift import detect_drift
from schema_evolver.models import ChangeKind, Column, Contract
from schema_evolver.profiler import profile_records


def kinds(changes):
    return {(c.kind, c.column) for c in changes}


def test_demo_drift(demo):
    *_, changes = demo
    k = kinds(changes)
    assert (ChangeKind.MISSING_COLUMN, "cust_id") in k
    assert (ChangeKind.MISSING_COLUMN, "region") in k
    assert (ChangeKind.NEW_COLUMN, "customer_id") in k
    assert (ChangeKind.TYPE_WIDEN, "loyalty_points") in k
    assert (ChangeKind.TYPE_WIDEN, "amount") in k
    assert (ChangeKind.NEW_COLUMN, "customer_email") in k


def test_small_ints_fit_short_contract():
    c = Contract("t", 1, [Column("s", "short")])
    assert detect_drift(c, profile_records([{"s": 7}, {"s": -300}])) == []


def test_overflowing_short_is_widen():
    c = Contract("t", 1, [Column("s", "short")])
    ch = detect_drift(c, profile_records([{"s": 70000}]))
    assert ch[0].kind == ChangeKind.TYPE_WIDEN and ch[0].new_type == "int"


def test_string_to_int_is_incompatible():
    c = Contract("t", 1, [Column("s", "string")])
    assert detect_drift(c, profile_records([{"s": 5}]))[0].kind == ChangeKind.TYPE_INCOMPATIBLE


def test_nulls_in_required():
    c = Contract("t", 1, [Column("id", "string", nullable=False)])
    ch = detect_drift(c, profile_records([{"id": "a"}, {"id": None}]))
    assert ch[0].kind == ChangeKind.NULLS_IN_REQUIRED


def test_all_null_column_is_missing():
    c = Contract("t", 1, [Column("x", "string")])
    assert detect_drift(c, profile_records([{"x": None}]))[0].kind == ChangeKind.MISSING_COLUMN


def test_rescued_column_ignored():
    c = Contract("t", 1, [Column("x", "string")])
    assert detect_drift(c, profile_records([{"x": "a", "_rescued_data": "{}"}])) == []
