"""Rename proposers. Proposals are hypotheses, never decisions.

HeuristicProposer is deterministic. LLMProposer wraps any completion function
(str prompt -> str response). Its output is parsed strictly and fails closed.
"""
from __future__ import annotations

import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Callable

from .models import Change, ChangeKind, Contract, RenameProposal
from .profiler import ColumnProfile

log = logging.getLogger("schema_evolver")

_ABBREV = {"cust": "customer", "amt": "amount", "qty": "quantity", "ts": "timestamp",
           "dt": "date", "id": "id", "num": "number", "nbr": "number", "addr": "address",
           "desc": "description", "cnt": "count", "prod": "product", "txn": "transaction"}


def _tokens(name: str) -> list[str]:
    parts = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower().split("_")
    return [_ABBREV.get(p, p) for p in parts if p]


def name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    token = len(set(ta) & set(tb)) / max(len(set(ta) | set(tb)), 1)
    char = SequenceMatcher(None, "_".join(ta), "_".join(tb)).ratio()
    return round(max(token, char), 3)


def _candidates(changes: list[Change]) -> tuple[list[str], list[str]]:
    missing = [c.column for c in changes if c.kind == ChangeKind.MISSING_COLUMN]
    new = [c.column for c in changes if c.kind == ChangeKind.NEW_COLUMN]
    return missing, new


class HeuristicProposer:
    source = "heuristic"

    def __init__(self, min_similarity: float = 0.6) -> None:
        self.min_similarity = min_similarity

    def propose(self, contract: Contract, changes: list[Change],
                batch: dict[str, ColumnProfile]) -> list[RenameProposal]:
        missing, new = _candidates(changes)
        out = []
        for old in missing:
            for nw in new:
                s = name_similarity(old, nw)
                if s >= self.min_similarity:
                    out.append(RenameProposal(old, nw, s, f"name similarity {s}", self.source))
        return out


PROMPT_TEMPLATE = """You map columns that disappeared from a data contract to columns that newly appeared.
Only metadata is provided. Return JSON only, no prose, in exactly this shape
{{"renames": [{{"old": "<missing column>", "new": "<new column>", "confidence": <0..1>, "rationale": "<short>"}}]}}
Return an empty list when unsure. Each old and new column may appear at most once.

Table {table} contract version {version}
Missing columns (from contract) {missing}
New columns (from batch) {new}
"""


class LLMProposer:
    source = "llm"

    def __init__(self, complete_fn: Callable[[str], str], max_attempts: int = 2,
                 backoff_s: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> None:
        self.complete_fn = complete_fn
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.sleep = sleep
        self.rejected: list[dict] = []
        self.errors: list[str] = []

    def build_prompt(self, contract: Contract, changes: list[Change],
                     batch: dict[str, ColumnProfile]) -> str:
        missing, new = _candidates(changes)
        cols = contract.by_name()
        m = [{"name": c, "type": cols[c].type} for c in missing]
        n = [batch[c].summary() for c in new]
        return PROMPT_TEMPLATE.format(table=contract.table, version=contract.version,
                                      missing=json.dumps(m), new=json.dumps(n))

    def propose(self, contract: Contract, changes: list[Change],
                batch: dict[str, ColumnProfile]) -> list[RenameProposal]:
        missing, new = _candidates(changes)
        if not missing or not new:
            return []
        prompt = self.build_prompt(contract, changes, batch)
        raw = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.complete_fn(prompt)
                break
            except Exception as exc:  # transport errors from any SDK
                self.errors.append(f"attempt {attempt}: {type(exc).__name__}")
                log.warning(json.dumps({"event": "llm_call_failed", "attempt": attempt,
                                        "error": type(exc).__name__}))
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_s * attempt)
        if raw is None:
            return []  # fail closed: no proposals, deterministic path continues
        return self._parse(raw, set(missing), set(new))

    def _parse(self, raw: str, missing: set[str], new: set[str]) -> list[RenameProposal]:
        text = raw.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
        if fence:
            text = fence.group(1)
        try:
            obj = json.loads(text)
            items = obj["renames"]
            if not isinstance(items, list):
                raise TypeError("renames is not a list")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.errors.append(f"unparseable response: {type(exc).__name__}")
            return []
        out, used_old, used_new = [], set(), set()
        for it in items:
            reason = None
            if not isinstance(it, dict):
                reason = "item is not an object"
            else:
                old, nw, conf = it.get("old"), it.get("new"), it.get("confidence")
                if old not in missing:
                    reason = "old column is not a missing column"
                elif nw not in new:
                    reason = "new column is not a new column"
                elif not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
                    reason = "confidence outside [0,1]"
                elif old in used_old or nw in used_new:
                    reason = "column mapped more than once"
            if reason:
                self.rejected.append({"item": it if isinstance(it, dict) else str(it)[:80],
                                      "reason": reason})
                continue
            used_old.add(old)
            used_new.add(nw)
            out.append(RenameProposal(old, nw, float(conf), str(it.get("rationale", ""))[:200],
                                      self.source))
        return out


def scripted_llm(response_text: str) -> Callable[[str], str]:
    """Deterministic stand-in for a model endpoint (demo and tests)."""
    def _fn(prompt: str) -> str:  # noqa: ARG001
        return response_text
    return _fn
