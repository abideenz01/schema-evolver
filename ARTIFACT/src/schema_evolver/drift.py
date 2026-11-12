"""Deterministic drift detection: contract vs observed batch profile.

No model is involved here. The LLM only ever sees the output of this step.
"""
from __future__ import annotations

from .models import INTEGRAL, Change, ChangeKind, Contract, parse_decimal, widening_kind
from .profiler import ColumnProfile

_INT_RANGE = {"byte": 2**7, "short": 2**15, "int": 2**31, "long": 2**63}


def _fits_integral(p: ColumnProfile, target: str) -> bool:
    lim = _INT_RANGE[target]
    lo, hi = p.num_min, p.num_max
    return lo is None or (-lim <= lo and hi <= lim - 1)


def writable_into(p: ColumnProfile, target: str) -> bool:
    """Can observed values be written into a column of `target` type unchanged?"""
    src = p.type
    if src == target:
        return True
    if target in INTEGRAL and src in INTEGRAL:
        return _fits_integral(p, target)
    if target in ("double", "float") and src in INTEGRAL:
        return True  # JSON whole numbers in a fractional column
    if parse_decimal(target) and src in INTEGRAL + ("double",):
        return False  # precision cannot be proven from JSON; treat as drift
    if target == "timestamp" and src == "date":
        return True
    if target == "string":
        return False  # a typed value where text was expected is a contract change
    return widening_kind(src, target) is not None


def detect_drift(contract: Contract, batch: dict[str, ColumnProfile],
                 runtime: str = "delta_4_0") -> list[Change]:
    changes: list[Change] = []
    cols = contract.by_name()
    for name, col in cols.items():
        p = batch.get(name)
        if p is None or p.nulls == p.count:
            changes.append(Change(ChangeKind.MISSING_COLUMN, name, old_type=col.type,
                                  detail="absent" if p is None else "all values null"))
            continue
        if not writable_into(p, col.type):
            kind = widening_kind(col.type, p.type, runtime)
            if kind:
                changes.append(Change(ChangeKind.TYPE_WIDEN, name, col.type, p.type, detail=kind))
            else:
                changes.append(Change(ChangeKind.TYPE_INCOMPATIBLE, name, col.type, p.type))
        if not col.nullable and p.nulls > 0:
            changes.append(Change(ChangeKind.NULLS_IN_REQUIRED, name, col.type, p.type,
                                  detail=f"null_rate={p.null_rate:.3f}"))
    for name, p in batch.items():
        if name not in cols and name != "_rescued_data":
            changes.append(Change(ChangeKind.NEW_COLUMN, name, new_type=p.type))
    order = {k: i for i, k in enumerate(ChangeKind)}
    return sorted(changes, key=lambda c: (order[c.kind], c.column))
