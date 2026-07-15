from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class GameConfig:
    game_id: str
    home_team: str
    away_team: str
    regulation_periods: int = 4
    regulation_period_seconds: int = 600
    overtime_period_seconds: int = 300

    def period_length(self, period: int) -> int:
        if period <= 0:
            raise ValueError("period must be positive")
        if period <= self.regulation_periods:
            return self.regulation_period_seconds
        return self.overtime_period_seconds


@dataclass
class PlayerStats:
    points: int = 0
    rebounds: int = 0
    assists: int = 0
    threes: int = 0
    steals: int = 0
    blocks: int = 0
    turnovers: int = 0
    fouls: int = 0
    minutes_seconds: int = 0

    def apply(self, stat: str, delta: int) -> None:
        if stat not in self.__dataclass_fields__:
            raise ValueError(f"unknown stat: {stat}")
        value = getattr(self, stat) + delta
        if value < 0:
            raise ValueError(f"{stat} cannot become negative")
        setattr(self, stat, value)


@dataclass
class PlayerState:
    player_id: str
    team_id: str
    active: bool = True
    on_court: bool = False
    injury_state: str = "healthy"
    current_stint_seconds: int = 0
    stats: PlayerStats = field(default_factory=PlayerStats)


@dataclass
class GameState:
    config: GameConfig
    sequence: int = 0
    period: int = 1
    clock_seconds: int = 600
    home_score: int = 0
    away_score: int = 0
    possession_team: str | None = None
    home_lineup: tuple[str, ...] = ()
    away_lineup: tuple[str, ...] = ()
    team_fouls: dict[str, int] = field(default_factory=dict)
    timeouts: dict[str, int] = field(default_factory=dict)
    players: dict[str, PlayerState] = field(default_factory=dict)
    last_source_timestamp_ms: int = 0
    last_received_timestamp_ms: int = 0
    last_event_id: str | None = None
    status: str = "scheduled"

    def score_for(self, team_id: str) -> int:
        if team_id == self.config.home_team:
            return self.home_score
        if team_id == self.config.away_team:
            return self.away_score
        raise ValueError(f"unknown team: {team_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
