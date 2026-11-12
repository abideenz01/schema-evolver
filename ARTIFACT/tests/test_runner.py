import json

import pytest

from schema_evolver.models import RenameProposal
from schema_evolver.planner import build_plan
from schema_evolver.policy import evaluate
from schema_evolver.runner import DryRunExecutor, TransientError, rollback_statements, run_plan


@pytest.fixture
def plan(demo):
    contract, ref, batch, changes = demo
    return build_plan(contract, evaluate(contract, changes,
                      [RenameProposal("cust_id", "customer_id", 0.9, "", "llm")], batch, ref))


class Flaky(DryRunExecutor):
    def __init__(self, fail_on, times, exc=TransientError):
        super().__init__()
        self.fail_on, self.times, self.exc = fail_on, times, exc

    def execute(self, sql):
        if self.fail_on in sql and self.times > 0:
            self.times -= 1
            raise self.exc("conflict")
        super().execute(sql)


def test_rerun_is_noop(plan, tmp_path):
    j = tmp_path / "j.json"
    ex1 = DryRunExecutor()
    run_plan(plan, ex1, j)
    ex2 = DryRunExecutor()
    run_plan(plan, ex2, j)
    assert len(ex1.statements) == len([s for s in plan.steps if not s.deferred])
    assert ex2.statements == []


def test_transient_retry(plan, tmp_path):
    sleeps = []
    ex = Flaky("ADD COLUMNS", 2)
    journal = run_plan(plan, ex, tmp_path / "j.json", sleep=sleeps.append, backoff_s=1)
    assert "add_columns" in journal["done"] and sleeps == [1, 2]


def test_retry_budget_exhausted_then_resume(plan, tmp_path):
    j = tmp_path / "j.json"
    with pytest.raises(TransientError):
        run_plan(plan, Flaky("UPDATE", 9), j, max_retries=2, sleep=lambda s: None)
    state = json.loads(j.read_text())
    assert state["failed"]["step"] == "backfill_customer_id"
    assert "add_columns" in state["done"]
    ex = DryRunExecutor()
    run_plan(plan, ex, j)
    assert ex.statements[0].startswith("UPDATE")  # resumed at the failed step
    assert json.loads(j.read_text())["failed"] is None


def test_fatal_error_not_retried(plan, tmp_path):
    ex = Flaky("ADD COLUMNS", 1, exc=ValueError)
    with pytest.raises(ValueError):
        run_plan(plan, ex, tmp_path / "j.json", sleep=lambda s: pytest.fail("slept"))


def test_journal_plan_mismatch(plan, tmp_path):
    j = tmp_path / "j.json"
    j.write_text(json.dumps({"plan_id": "other", "done": []}))
    with pytest.raises(RuntimeError, match="journal belongs"):
        run_plan(plan, DryRunExecutor(), j)


def test_deferred_only_when_asked(plan, tmp_path):
    ex = DryRunExecutor()
    run_plan(plan, ex, tmp_path / "j.json", include_deferred=True)
    assert any("DROP COLUMNS" in s for s in ex.statements)


def test_refuses_quarantined(plan, tmp_path):
    plan.verdict = "QUARANTINE_BATCH"
    with pytest.raises(RuntimeError):
        run_plan(plan, DryRunExecutor(), tmp_path / "j.json")


def test_rollback_order(plan, tmp_path):
    journal = run_plan(plan, DryRunExecutor(), tmp_path / "j.json")
    rb = rollback_statements(plan, journal)
    assert rb[0].startswith("CREATE OR REPLACE VIEW") and "`cust_id`" in rb[0]
    assert rb[1] == f"RESTORE TABLE {plan.table} TO VERSION AS OF 0"
    assert len(rb) == 2


def test_rollback_without_pre_version(plan):
    rb = rollback_statements(plan, {"done": ["add_columns"], "pre_version": None})
    assert rb[0].startswith("-- cannot restore")
