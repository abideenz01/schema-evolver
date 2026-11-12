"""Idempotent, resumable plan execution with a checkpoint journal.

Each completed step is recorded atomically. Re-running the same plan skips
completed steps; a different plan against the same journal is refused.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .planner import Plan

log = logging.getLogger("schema_evolver")


class TransientError(RuntimeError):
    """Retryable failure (concurrent commit conflict, throttling, timeout)."""


class Executor(Protocol):
    def execute(self, sql: str) -> Any: ...


class DryRunExecutor:
    """Collects statements instead of running them."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)

    def current_version(self, table: str) -> int:  # noqa: ARG002
        return 0


class SparkExecutor:  # pragma: no cover - requires a Spark session with Delta
    def __init__(self, spark: Any) -> None:
        self.spark = spark

    def execute(self, sql: str) -> Any:
        return self.spark.sql(sql)

    def current_version(self, table: str) -> int:
        return int(self.spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").collect()[0]["version"])


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def load_journal(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def run_plan(plan: Plan, executor: Executor, journal_path: str | Path,
             include_deferred: bool = False, max_retries: int = 3, backoff_s: float = 1.0,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    if plan.verdict != "PROCEED":
        raise RuntimeError(f"plan verdict is {plan.verdict}; refusing to execute")
    jp = Path(journal_path)
    journal = load_journal(jp)
    if journal and journal.get("plan_id") != plan.plan_id:
        raise RuntimeError(f"journal belongs to plan {journal.get('plan_id')}, not {plan.plan_id}")
    if not journal:
        pre = executor.current_version(plan.table) if hasattr(executor, "current_version") else None
        journal = {"plan_id": plan.plan_id, "table": plan.table, "pre_version": pre,
                   "done": [], "failed": None}
        _write_atomic(jp, journal)

    for step in plan.steps:
        if step.id in journal["done"]:
            log.info(json.dumps({"event": "step_skipped", "step": step.id, "reason": "already done"}))
            continue
        if step.deferred and not include_deferred:
            log.info(json.dumps({"event": "step_deferred", "step": step.id}))
            continue
        attempt = 0
        while True:
            attempt += 1
            t0 = time.perf_counter()
            try:
                executor.execute(step.sql)
                break
            except TransientError as exc:
                if attempt > max_retries:
                    journal["failed"] = {"step": step.id, "error": str(exc)[:200], "attempts": attempt}
                    _write_atomic(jp, journal)
                    raise
                log.warning(json.dumps({"event": "step_retry", "step": step.id, "attempt": attempt}))
                sleep(backoff_s * 2 ** (attempt - 1))
            except Exception as exc:
                journal["failed"] = {"step": step.id, "error": str(exc)[:200], "attempts": attempt}
                _write_atomic(jp, journal)
                raise
        journal["done"].append(step.id)
        journal["failed"] = None
        _write_atomic(jp, journal)
        log.info(json.dumps({"event": "step_done", "step": step.id, "phase": step.phase,
                             "attempts": attempt, "ms": round((time.perf_counter() - t0) * 1000, 1)}))
    return journal


def rollback_statements(plan: Plan, journal: dict[str, Any]) -> list[str]:
    """Reverse-order rollback for completed steps.

    A view switch is reverted by restoring the previous view (instant, no data loss).
    Schema steps revert with RESTORE, which also discards writes made after
    pre_version, so pause writers first.
    """
    done = set(journal.get("done", []))
    pre = journal.get("pre_version")
    out, restore_added = [], False
    for step in reversed(plan.steps):
        if step.id not in done or not step.rollback_sql:
            continue
        if step.rollback_sql.startswith("RESTORE"):
            if restore_added:
                continue
            if pre is None:
                out.append(f"-- cannot restore {plan.table}: pre_version unknown")
                continue
            restore_added = True
        out.append(step.rollback_sql.replace("{pre_version}", str(pre)))
    return out
