"""Infer a batch schema and a privacy-safe column profile from JSON records.

Profiles hold hashed value fingerprints and numeric summaries, never raw values,
so they can be logged, stored with the contract, and sent to an LLM prompt.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import SchemaError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_INT32 = 2**31 - 1
_RANK = {"boolean": 0, "int": 1, "long": 2, "double": 3, "date": 4, "timestamp": 5, "string": 6}


def _value_type(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "int" if -_INT32 - 1 <= v <= _INT32 else "long"
    if isinstance(v, float):
        return "double"
    if isinstance(v, str):
        if _DATE_RE.match(v):
            return "date"
        if _TS_RE.match(v):
            return "timestamp"
        return "string"
    return "string"  # nested values are treated as opaque strings in this tool


def _merge(a: str | None, b: str | None) -> str | None:
    if a is None or a == b:
        return b if a is None else a
    if b is None:
        return a
    numeric = {"int", "long", "double"}
    if a in numeric and b in numeric:
        return max(a, b, key=_RANK.get)
    if {a, b} == {"date", "timestamp"}:
        return "timestamp"
    return "string"


_LETTERS = re.compile(r"[A-Za-z]+")


def shape(v: Any) -> str:
    """Format signature. Digits become 9, short letter runs (<=3) stay literal,
    longer runs become 'a'. 'C00123' -> 'C99999', 'WINTER10' -> 'a99'."""
    s = re.sub(r"\d", "9", str(v))
    return _LETTERS.sub(lambda m: m.group(0) if len(m.group(0)) <= 3 else "a", s)[:32]


def fingerprint(v: Any, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{v}".encode()).hexdigest()[:16]


@dataclass
class ColumnProfile:
    name: str
    type: str
    count: int = 0
    nulls: int = 0
    distinct_fps: set[str] = field(default_factory=set)
    num_min: float | None = None
    num_max: float | None = None
    num_sum: float = 0.0
    num_n: int = 0
    shapes: dict[str, int] = field(default_factory=dict)

    @property
    def null_rate(self) -> float:
        return self.nulls / self.count if self.count else 0.0

    @property
    def mean(self) -> float | None:
        return self.num_sum / self.num_n if self.num_n else None

    def summary(self) -> dict[str, Any]:
        """Metadata only; safe to log or put in an LLM prompt."""
        return {"name": self.name, "type": self.type, "null_rate": round(self.null_rate, 3),
                "distinct_est": len(self.distinct_fps),
                "min": self.num_min, "max": self.num_max,
                "mean": None if self.mean is None else round(self.mean, 4)}

    def to_dict(self) -> dict[str, Any]:
        d = self.summary()
        d.update(count=self.count, nulls=self.nulls, fps=sorted(self.distinct_fps),
                 num_sum=self.num_sum, num_n=self.num_n, shapes=self.shapes)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ColumnProfile":
        return cls(name=d["name"], type=d["type"], count=d["count"], nulls=d["nulls"],
                   distinct_fps=set(d["fps"]), num_min=d["min"], num_max=d["max"],
                   num_sum=d["num_sum"], num_n=d["num_n"], shapes=dict(d.get("shapes", {})))


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{path}:{i} is not valid JSON ({exc.msg})") from exc
        if not isinstance(obj, dict):
            raise SchemaError(f"{path}:{i} must be a JSON object")
        rows.append(obj)
    if not rows:
        raise SchemaError(f"{path} contains no records")
    return rows


def profile_records(records: Iterable[dict[str, Any]], salt: str = "schema-evolver",
                    max_distinct: int = 5000) -> dict[str, ColumnProfile]:
    """Single pass profile. Columns absent from a record count as null."""
    records = list(records)
    names: list[str] = []
    for r in records:
        for k in r:
            if k not in names:
                names.append(k)
    profiles: dict[str, ColumnProfile] = {}
    for name in names:
        inferred: str | None = None
        p = ColumnProfile(name=name, type="string")
        for r in records:
            v = r.get(name)
            p.count += 1
            if v is None:
                p.nulls += 1
                continue
            inferred = _merge(inferred, _value_type(v))
            if len(p.distinct_fps) < max_distinct:
                p.distinct_fps.add(fingerprint(v, salt))
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                sh = shape(v)
                if sh in p.shapes or len(p.shapes) < 200:
                    p.shapes[sh] = p.shapes.get(sh, 0) + 1
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                p.num_min = v if p.num_min is None else min(p.num_min, v)
                p.num_max = v if p.num_max is None else max(p.num_max, v)
                p.num_sum += v
                p.num_n += 1
        p.type = inferred or "string"
        profiles[name] = p
    return profiles
