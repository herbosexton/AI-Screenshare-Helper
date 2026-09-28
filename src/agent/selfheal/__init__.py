"""Self-correction loop: record every turn, detect wrong decisions, file incidents for the fixer."""

from src.agent.selfheal.detector import Finding, FailureDetector
from src.agent.selfheal.incidents import Incident, IncidentStore
from src.agent.selfheal.loop import SelfHealLoop
from src.agent.selfheal.recorder import TurnRecord, TurnRecorder

__all__ = [
    "FailureDetector",
    "Finding",
    "Incident",
    "IncidentStore",
    "SelfHealLoop",
    "TurnRecord",
    "TurnRecorder",
]
