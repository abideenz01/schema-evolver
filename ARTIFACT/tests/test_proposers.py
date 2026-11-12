import json

from schema_evolver.proposers import HeuristicProposer, LLMProposer, name_similarity, scripted_llm


def test_name_similarity_expands_abbreviations():
    assert name_similarity("cust_id", "customer_id") == 1.0
    assert name_similarity("region", "promo_code") < 0.5


def test_heuristic(demo):
    contract, _, batch, changes = demo
    pairs = {(p.old, p.new) for p in HeuristicProposer().propose(contract, changes, batch)}
    assert ("cust_id", "customer_id") in pairs


def test_llm_valid_and_schema_rejections(demo, ex):
    contract, _, batch, changes = demo
    llm = LLMProposer(scripted_llm((ex / "llm_response.json").read_text()))
    props = llm.propose(contract, changes, batch)
    assert {(p.old, p.new) for p in props} == {("cust_id", "customer_id"), ("region", "promo_code")}
    assert llm.rejected[0]["reason"] == "old column is not a missing column"


def test_llm_fenced_json(demo):
    contract, _, batch, changes = demo
    txt = '```json\n{"renames": [{"old": "cust_id", "new": "customer_id", "confidence": 0.9}]}\n```'
    assert len(LLMProposer(scripted_llm(txt)).propose(contract, changes, batch)) == 1


def test_llm_malformed_fails_closed(demo):
    contract, _, batch, changes = demo
    llm = LLMProposer(scripted_llm("Sure! cust_id became customer_id."))
    assert llm.propose(contract, changes, batch) == []
    assert llm.errors


def test_llm_rejects_bad_items(demo):
    contract, _, batch, changes = demo
    resp = {"renames": [
        {"old": "cust_id", "new": "customer_id", "confidence": 1.4},
        {"old": "cust_id", "new": "customer_id", "confidence": True},
        {"old": "cust_id", "new": "customer_id", "confidence": 0.9},
        {"old": "cust_id", "new": "promo_code", "confidence": 0.9},
        "not an object"]}
    llm = LLMProposer(scripted_llm(json.dumps(resp)))
    props = llm.propose(contract, changes, batch)
    assert len(props) == 1
    reasons = [r["reason"] for r in llm.rejected]
    assert reasons.count("confidence outside [0,1]") == 2
    assert "column mapped more than once" in reasons
    assert "item is not an object" in reasons


def test_llm_retry_then_success(demo):
    contract, _, batch, changes = demo
    calls, sleeps = [], []

    def flaky(prompt):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("slow")
        return '{"renames": []}'
    llm = LLMProposer(flaky, max_attempts=2, sleep=sleeps.append)
    assert llm.propose(contract, changes, batch) == []
    assert len(calls) == 2 and sleeps == [0.5]


def test_llm_all_attempts_fail(demo):
    contract, _, batch, changes = demo

    def down(prompt):
        raise ConnectionError("down")
    llm = LLMProposer(down, max_attempts=3, sleep=lambda s: None)
    assert llm.propose(contract, changes, batch) == []
    assert len(llm.errors) == 3


def test_prompt_contains_metadata_only(demo):
    contract, _, batch, changes = demo
    prompt = LLMProposer(scripted_llm("{}")).build_prompt(contract, changes, batch)
    assert "customer_email" in prompt
    assert "@example.com" not in prompt and "WINTER10" not in prompt


def test_llm_not_called_without_candidates(demo):
    contract, _, batch, _ = demo
    called = []
    LLMProposer(lambda p: called.append(p) or "{}").propose(contract, [], batch)
    assert called == []
