import json

import pytest

from schema_evolver.models import SchemaError
from schema_evolver.profiler import ColumnProfile, profile_records, read_jsonl, shape


def test_type_inference_and_merge():
    p = profile_records([{"a": 1, "b": 1.5, "c": "2026-01-01", "d": "2026-01-01T10:00:00", "e": True},
                         {"a": 3_000_000_000, "b": 2, "c": "2026-01-02T00:00:00", "d": None, "e": "x"}])
    assert p["a"].type == "long"
    assert p["b"].type == "double"
    assert p["c"].type == "timestamp"
    assert p["d"].type == "timestamp" and p["d"].nulls == 1
    assert p["e"].type == "string"


def test_absent_key_counts_as_null():
    p = profile_records([{"a": 1}, {"b": 2}])
    assert p["a"].null_rate == 0.5 and p["b"].null_rate == 0.5


def test_summary_has_no_raw_values():
    p = profile_records([{"email": "jane@example.com"}])["email"]
    blob = json.dumps(p.summary())
    assert "jane" not in blob and "example.com" not in blob


def test_roundtrip():
    p = profile_records([{"a": 1}, {"a": 5}])["a"]
    q = ColumnProfile.from_dict(p.to_dict())
    assert q.distinct_fps == p.distinct_fps and q.mean == p.mean


def test_shape():
    assert shape("C00123") == "C99999"
    assert shape("WINTER10") == "a99"


def test_read_jsonl_errors(tmp_path):
    bad = tmp_path / "b.jsonl"
    bad.write_text('{"a": 1}\n{not json}\n')
    with pytest.raises(SchemaError, match=":2"):
        read_jsonl(bad)
    arr = tmp_path / "c.jsonl"
    arr.write_text("[1,2]\n")
    with pytest.raises(SchemaError):
        read_jsonl(arr)
    empty = tmp_path / "e.jsonl"
    empty.write_text("\n")
    with pytest.raises(SchemaError):
        read_jsonl(empty)
