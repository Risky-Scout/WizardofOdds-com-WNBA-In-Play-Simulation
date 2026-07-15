"""WNBA in-play simulation reference implementation."""

from .domain import GameConfig, GameState, PlayerState, PlayerStats
from .events import GameEvent
from .state_engine import EventSequenceError, InvalidStateError, StateEngine

__all__ = [
    "GameConfig",
    "GameEvent",
    "GameState",
    "PlayerState",
    "PlayerStats",
    "EventSequenceError",
    "InvalidStateError",
    "StateEngine",
    "SimulationRequest",
    "SimulationService",
    "MarketSpec",
    "RunMetadata",
    "SimulationReport",
]

from .service import SimulationRequest, SimulationService
from .contracts import MarketSpec, RunMetadata, SimulationReport
