from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import math
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(UTC)


class RecommendationStatus(StrEnum):
    WATCH = "WATCH"
    QUALIFIED = "QUALIFIED"
    PUBLISHED = "PUBLISHED"
    PRICE_CHANGED = "PRICE_CHANGED"
    WITHDRAWN = "WITHDRAWN"
    SUSPENDED = "SUSPENDED"
    SETTLED = "SETTLED"


@dataclass(frozen=True)
class GameState:
    canonical_game_id: str
    source_game_id: str
    home_team: str
    away_team: str
    period: int
    clock_seconds: float
    home_score: int
    away_score: int
    possession_team: str | None
    event_sequence: int
    source_timestamp: datetime
    received_timestamp: datetime
    status: str = "in_progress"
    home_lineup: tuple[str, ...] = ()
    away_lineup: tuple[str, ...] = ()
    unresolved_review: bool = False
    sequence_gap: bool = False
    reconciliation_ok: bool = True

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utc_now() - self.received_timestamp).total_seconds())

    @property
    def margin(self) -> int:
        return self.home_score - self.away_score

    @property
    def regulation_seconds_remaining(self) -> float:
        if self.period > 4:
            return self.clock_seconds
        return self.clock_seconds + max(4 - self.period, 0) * 600


@dataclass(frozen=True)
class PlayerLiveState:
    canonical_player_id: str
    display_name: str
    team: str
    on_court: bool
    active: bool
    minutes_played: float
    current_stint_seconds: float
    fouls: int
    injury_state: str
    points: int
    rebounds: int
    assists: int
    threes: int
    steals: int = 0
    blocks: int = 0
    turnovers: int = 0


@dataclass(frozen=True)
class PlayerRateProfile:
    canonical_player_id: str
    expected_remaining_minutes: float
    remaining_minutes_sd: float
    points_per_minute: float
    rebounds_per_minute: float
    assists_per_minute: float
    threes_per_minute: float
    usage_multiplier: float = 1.0
    pace_multiplier: float = 1.0
    role_uncertainty: float = 0.03
    target_total_minutes: float | None = None


@dataclass(frozen=True)
class MarketOffer:
    provider: str
    bookmaker_key: str
    bookmaker_title: str
    provider_event_id: str
    canonical_game_id: str
    market_key: str
    outcome_name: str
    side: str
    line: float | None
    american_odds: int
    decimal_odds: float
    player_name: str | None
    canonical_player_id: str | None
    last_update: datetime
    received_timestamp: datetime
    deep_link: str | None = None
    available: bool = True

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utc_now() - self.received_timestamp).total_seconds())

    @property
    def offer_id(self) -> str:
        player = self.canonical_player_id or self.outcome_name
        return (
            f"{self.bookmaker_key}|{self.canonical_game_id}|{self.market_key}|"
            f"{player}|{self.side}|{self.line}"
        )


@dataclass(frozen=True)
class ProbabilityEstimate:
    market_key: str
    line: float | None
    p_win: float
    p_push: float
    p_loss: float
    fair_decimal_odds: float
    projected_mean: float
    projected_sd: float
    simulation_count: int
    monte_carlo_se: float
    calibration_se: float
    model_se: float
    calibration_score: float
    calibrator_id: str
    model_version: str
    pmf: Mapping[int, float] = field(default_factory=dict)

    def validate(self) -> None:
        values = (self.p_win, self.p_push, self.p_loss)
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            raise ValueError("probabilities must be finite and in [0,1]")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("win/push/loss probabilities must sum to one")
        if self.simulation_count <= 0:
            raise ValueError("simulation_count must be positive")
        if not 0 <= self.calibration_score <= 1:
            raise ValueError("calibration_score must be in [0,1]")

    @property
    def total_uncertainty(self) -> float:
        return math.sqrt(
            self.monte_carlo_se**2
            + self.calibration_se**2
            + self.model_se**2
        )


@dataclass(frozen=True)
class MarketConsensus:
    market_key: str
    line: float | None
    side: str
    no_vig_probability: float | None
    book_count: int
    median_decimal_odds: float | None
    best_decimal_odds: float | None
    best_bookmaker: str | None
    dispersion: float | None
    as_of: datetime


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    status: RecommendationStatus
    canonical_game_id: str
    canonical_player_id: str | None
    player_name: str | None
    market_key: str
    side: str
    line: float | None
    bookmaker_key: str
    bookmaker_title: str
    decimal_odds: float
    american_odds: int
    model_probability: float
    fair_decimal_odds: float
    no_vig_consensus_probability: float | None
    raw_edge: float
    expected_roi: float
    conservative_roi: float
    required_roi: float
    confidence_grade: str
    projected_mean: float
    projected_sd: float
    p_push: float
    total_uncertainty: float
    game_state_age_seconds: float
    market_age_seconds: float
    book_count: int
    simulation_count: int
    calibrator_id: str
    model_version: str
    event_sequence: int
    generated_at: datetime
    expires_at: datetime
    pmf: Mapping[int, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    deep_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from .odds_math import decimal_to_american

        value = asdict(self)
        value["status"] = self.status.value
        for key in ("generated_at", "expires_at"):
            value[key] = value[key].isoformat()

        # Decimal prices remain internal implementation details. The public
        # WizardofOdds.com product is American-odds only.
        value.pop("decimal_odds", None)
        fair_decimal = value.pop("fair_decimal_odds", None)
        value["fair_american_odds"] = (
            decimal_to_american(float(fair_decimal))
            if fair_decimal is not None
            and math.isfinite(float(fair_decimal))
            and float(fair_decimal) > 1.0
            else None
        )
        value["odds_format"] = "american"
        return value
