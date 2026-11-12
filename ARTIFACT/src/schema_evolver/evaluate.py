"""Evaluate rename acceptance policies on labeled cases.

Compares accepting model proposals on confidence alone with accepting them only
after the evidence gate. Cases in examples/ are synthetic; replace them with
labeled drift events from your own incident history to measure your workload.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .policy import PolicyConfig, rename_evidence
from .profiler import profile_records


def _profile(name: str, values: list[Any]):
    return profile_records([{name: v} for v in values])[name]


def _metrics(pred: list[bool], truth: list[bool]) -> dict[str, Any]:
    tp = sum(p and t for p, t in zip(pred, truth, strict=True))
    fp = sum(p and not t for p, t in zip(pred, truth, strict=True))
    fn = sum(t and not p for p, t in zip(pred, truth, strict=True))
    return {"accepted": tp + fp, "true_accepts": tp, "false_accepts": fp, "missed": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def evaluate_cases(cases: list[dict[str, Any]], confidence_threshold: float = 0.7,
                   cfg: PolicyConfig | None = None) -> dict[str, Any]:
    cfg = cfg or PolicyConfig()
    truth, llm_only, gated, rows = [], [], [], []
    for c in cases:
        for key in ("id", "old", "new", "llm_confidence", "is_rename"):
            if key not in c:
                raise ValueError(f"case missing '{key}': {c.get('id', '?')}")
        old = _profile(c["old"]["name"], c["old"]["values"])
        new = _profile(c["new"]["name"], c["new"]["values"])
        ev = rename_evidence(c["old"]["type"], old, new, cfg)
        proposed = c["llm_confidence"] >= confidence_threshold
        truth.append(bool(c["is_rename"]))
        llm_only.append(proposed)
        gated.append(proposed and ev["verified"])
        rows.append({"id": c["id"], "truth": c["is_rename"], "llm_conf": c["llm_confidence"],
                     "verified": ev["verified"], "failed": ev.get("failed")})
    return {"n_cases": len(cases), "confidence_threshold": confidence_threshold,
            "llm_only": _metrics(llm_only, truth), "gated": _metrics(gated, truth), "cases": rows}


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("cases file must be a JSON list")
    return data


def estimate_impact(events_per_month: dict[str, float], manual_minutes: dict[str, float],
                    auto_fraction: dict[str, float]) -> dict[str, float]:
    """hours_saved = sum(events * auto_fraction * manual_minutes) / 60 per change class.

    All inputs are yours to supply. The tool ships no default savings figure.
    """
    total = 0.0
    out: dict[str, float] = {}
    for k, n in events_per_month.items():
        h = n * auto_fraction.get(k, 0.0) * manual_minutes.get(k, 0.0) / 60
        out[k] = round(h, 2)
        total += h
    out["total_hours_per_month"] = round(total, 2)
    return out
