import json

from schema_evolver.cli import main
from schema_evolver.evaluate import estimate_impact, evaluate_cases, load_cases


def _plan_args(ex, out, batch="batch_v2_drifted.jsonl"):
    return ["plan", "--contract", str(ex / "contract_v1.json"),
            "--reference", str(ex / "batch_v1_reference.jsonl"), "--batch", str(ex / batch),
            "--consumers", str(ex / "consumers.json"),
            "--llm-response", str(ex / "llm_response.json"), "--out", str(out)]


def test_cli_plan_matches_expected(ex, tmp_path):
    assert main(_plan_args(ex, tmp_path)) == 0
    got = (tmp_path / "plan.sql").read_text()
    assert got == (ex / "expected_output" / "plan.sql").read_text()


def test_cli_quarantine_exit_code(ex, tmp_path):
    assert main(_plan_args(ex, tmp_path, "batch_wrong_source.jsonl")) == 2


def test_cli_bad_input_exit_code(tmp_path, ex):
    bad = tmp_path / "c.json"
    bad.write_text("{")
    args = _plan_args(ex, tmp_path)
    args[2] = str(bad)
    assert main(args) == 1


def test_cli_apply_dry_run(ex, tmp_path, capsys):
    main(_plan_args(ex, tmp_path))
    capsys.readouterr()
    assert main(["apply", "--plan", str(tmp_path / "plan.json"), "--journal", str(tmp_path / "j.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["done"][-1] == "switch_view"


def test_eval_regression_on_synthetic_cases(ex):
    res = evaluate_cases(load_cases(ex / "eval_cases.json"))
    assert res["n_cases"] == 16
    assert res["llm_only"]["false_accepts"] == 6
    assert res["gated"]["false_accepts"] == 2
    assert res["gated"]["precision"] > res["llm_only"]["precision"]


def test_estimate_impact():
    r = estimate_impact({"add": 10, "rename": 2}, {"add": 30, "rename": 120}, {"add": 1.0, "rename": 0.5})
    assert r == {"add": 5.0, "rename": 2.0, "total_hours_per_month": 7.0}
