"""Core data structures and the type-widening rule table.

Everything here is pure and deterministic so it can be unit tested without
Spark, Delta, or a model endpoint.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SIMPLE_TYPES = {
    "byte", "short", "int", "long", "float", "double", "string",
    "boolean", "date", "timestamp", "timestamp_ntz",
}
_DECIMAL_RE = re.compile(r"^decimal\((\d+),\s*(\d+)\)$")


class SchemaError(ValueError):
    """Raised for malformed contracts, batches, or type names."""


def normalize_type(raw: str) -> str:
    t = str(raw).strip().lower().replace(" ", "")
    aliases = {"bigint": "long", "integer": "int", "smallint": "short",
               "tinyint": "byte", "bool": "boolean", "str": "string",
               "timestampntz": "timestamp_ntz", "real": "float"}
    t = aliases.get(t, t)
    if t in SIMPLE_TYPES or _DECIMAL_RE.match(t):
        return t
    raise SchemaError(f"unsupported type '{raw}'")


def parse_decimal(t: str) -> tuple[int, int] | None:
    m = _DECIMAL_RE.match(t)
    return (int(m.group(1)), int(m.group(2))) if m else None


INTEGRAL = ("byte", "short", "int", "long")
_MIN_DECIMAL = {"byte": 10, "short": 10, "int": 10, "long": 20}

# Supported widenings, per Delta Lake type widening docs (see EVIDENCE.md).
_WIDEN_3_2 = {"byte": {"short", "int"}, "short": {"int"}}
_WIDEN_4_0 = {
    "byte": {"short", "int", "long", "double"},
    "short": {"int", "long", "double"},
    "int": {"long", "double"},
    "long": set(),
    "float": {"double"},
    "date": {"timestamp_ntz"},
}


def widening_kind(src: str, dst: str, runtime: str = "delta_4_0") -> str | None:
    """Return 'integral', 'float', 'decimal', 'int_to_fractional', 'date',
    or None if src->dst is not a supported widening on the runtime."""
    src, dst = normalize_type(src), normalize_type(dst)
    if src == dst:
        return None
    if runtime == "delta_3_2":
        return "integral" if dst in _WIDEN_3_2.get(src, set()) else None
    if runtime != "delta_4_0":
        raise SchemaError(f"unknown runtime '{runtime}'")
    sd, dd = parse_decimal(src), parse_decimal(dst)
    if sd and dd:
        k1, k2 = dd[0] - sd[0], dd[1] - sd[1]
        return "decimal" if (k1 >= k2 >= 0 and (k1 or k2)) else None
    if src in INTEGRAL and dd:
        # integer -> decimal needs enough integer digits (p - s)
        return "int_to_fractional" if dd[0] - dd[1] >= _MIN_DECIMAL[src] else None
    if dst in _WIDEN_4_0.get(src, set()):
        if src in INTEGRAL and dst == "double":
            return "int_to_fractional"
        if src in INTEGRAL:
            return "integral"
        return "float" if src == "float" else "date"
    return None


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    pii: bool = False

    def __post_init__(self) -> None:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.name):
            raise SchemaError(f"invalid column name '{self.name}'")
        object.__setattr__(self, "type", normalize_type(self.type))


@dataclass
class Contract:
    table: str
    version: int
    columns: list[Column]
    aliases: dict[str, str] = field(default_factory=dict)  # old_name -> new_name

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise SchemaError("duplicate column names in contract")
        if self.version < 1:
            raise SchemaError("contract version must be >= 1")

    def by_name(self) -> dict[str, Column]:
        return {c.name: c for c in self.columns}

    @classmethod
    def load(cls, path: str | Path) -> "Contract":
        try:
            raw = json.loads(Path(path).read_text())
            return cls(
                table=raw["table"], version=int(raw["version"]),
                columns=[Column(**c) for c in raw["columns"]],
                aliases=dict(raw.get("aliases", {})),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SchemaError(f"invalid contract file {path}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "version": self.version,
                "columns": [asdict(c) for c in self.columns],
                "aliases": dict(sorted(self.aliases.items()))}


class ChangeKind(str, Enum):
    NEW_COLUMN = "NEW_COLUMN"
    MISSING_COLUMN = "MISSING_COLUMN"
    TYPE_WIDEN = "TYPE_WIDEN"
    TYPE_INCOMPATIBLE = "TYPE_INCOMPATIBLE"
    NULLS_IN_REQUIRED = "NULLS_IN_REQUIRED"


class Action(str, Enum):
    AUTO_APPLY = "AUTO_APPLY"            # additive or safe widening
    EXPAND_CONTRACT = "EXPAND_CONTRACT"  # verified rename, zero-downtime path
    FILL_NULL = "FILL_NULL"              # nullable column no longer sent; keep it
    RESCUE = "RESCUE"                    # land in _rescued_data, needs review
    BLOCK = "BLOCK"                      # batch-level stop, human decision


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    column: str
    old_type: str | None = None
    new_type: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RenameProposal:
    old: str
    new: str
    confidence: float
    rationale: str
    source: str  # "heuristic" | "llm"


@dataclass
class Decision:
    action: Action
    column: str
    reason: str
    change_kind: str
    new_column: str | None = None
    old_type: str | None = None
    new_type: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d
