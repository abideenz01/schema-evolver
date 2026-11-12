"""schema_evolver: bounded, evidence-gated schema evolution for Delta Lake tables."""
from .drift import detect_drift
from .models import Action, Column, Contract, SchemaError
from .planner import apply_writer_mapping, build_plan
from .policy import PolicyConfig, evaluate
from .profiler import profile_records
from .proposers import HeuristicProposer, LLMProposer

__all__ = ["Action", "Column", "Contract", "SchemaError", "PolicyConfig", "HeuristicProposer",
           "LLMProposer", "apply_writer_mapping", "build_plan", "detect_drift", "evaluate",
           "profile_records"]
__version__ = "0.1.0"
