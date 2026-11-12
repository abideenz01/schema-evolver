import pytest

from schema_evolver.models import Column, Contract, SchemaError, normalize_type, widening_kind


@pytest.mark.parametrize("raw,norm", [("BIGINT", "long"), ("Integer", "int"), ("decimal(10, 2)", "decimal(10,2)"),
                                      ("timestampNTZ", "timestamp_ntz")])
def test_normalize(raw, norm):
    assert normalize_type(raw) == norm


def test_unknown_type_rejected():
    with pytest.raises(SchemaError):
        normalize_type("map<string,int>")


@pytest.mark.parametrize("src,dst,rt,kind", [
    ("int", "long", "delta_4_0", "integral"),
    ("int", "long", "delta_3_2", None),           # not supported in 3.2
    ("short", "int", "delta_3_2", "integral"),
    ("float", "double", "delta_4_0", "float"),
    ("long", "double", "delta_4_0", None),         # not in the supported table
    ("int", "double", "delta_4_0", "int_to_fractional"),
    ("int", "decimal(10,0)", "delta_4_0", "int_to_fractional"),
    ("int", "decimal(9,0)", "delta_4_0", None),
    ("long", "decimal(19,0)", "delta_4_0", None),
    ("decimal(10,1)", "decimal(12,3)", "delta_4_0", "decimal"),
    ("decimal(10,2)", "decimal(10,3)", "delta_4_0", None),  # k1 < k2
    ("date", "timestamp_ntz", "delta_4_0", "date"),
    ("long", "int", "delta_4_0", None),
    ("string", "int", "delta_4_0", None),
])
def test_widening_table(src, dst, rt, kind):
    assert widening_kind(src, dst, rt) == kind


def test_unknown_runtime():
    with pytest.raises(SchemaError):
        widening_kind("int", "long", "delta_9")


def test_column_name_validation():
    with pytest.raises(SchemaError):
        Column("bad name; drop", "string")


def test_contract_duplicates():
    with pytest.raises(SchemaError):
        Contract("t", 1, [Column("a", "int"), Column("a", "long")])


def test_contract_load_malformed(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"table": "t"}')
    with pytest.raises(SchemaError):
        Contract.load(p)
