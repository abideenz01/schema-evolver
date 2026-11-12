import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EX = ROOT / "examples"


@pytest.fixture
def ex():
    return EX


@pytest.fixture
def demo(ex):
    from schema_evolver.drift import detect_drift
    from schema_evolver.models import Contract
    from schema_evolver.profiler import profile_records, read_jsonl
    contract = Contract.load(ex / "contract_v1.json")
    ref = profile_records(read_jsonl(ex / "batch_v1_reference.jsonl"))
    batch = profile_records(read_jsonl(ex / "batch_v2_drifted.jsonl"))
    return contract, ref, batch, detect_drift(contract, batch)
