from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


FORBIDDEN_PUBLIC_FIELDS = {
    "clv_adj_edge",
    "clv_decay_adjusted_edge",
    "clv_proxy",
}


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    commit_sha: str
    model_version: str
    calibration_version: str
    event_sequence: int
    as_of_timestamp_ms: int
    source_timestamp_ms: int
    input_fingerprint: str

    def validate(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.commit_sha:
            raise ValueError("commit_sha is required")
        if not self.model_version:
            raise ValueError("model_version is required")
        if not self.calibration_version:
            raise ValueError("calibration_version is required")
        if self.event_sequence < 0:
            raise ValueError("event_sequence cannot be negative")
        if self.as_of_timestamp_ms < self.source_timestamp_ms:
            raise ValueError("as-of timestamp precedes source timestamp")
        if not self.input_fingerprint:
            raise ValueError("input_fingerprint is required")


@dataclass(frozen=True)
class MarketSpec:
    market_id: str
    market_type: str
    line: float
    side: str = "over"
    player_id: str | None = None
    stats: tuple[str, ...] = ()
    reference_decimal_odds: float | None = None

    def validate(self) -> None:
        if not self.market_id:
            raise ValueError("market_id is required")
        if self.market_type not in {
            "player_prop",
            "game_total",
            "home_spread",
            "moneyline_home",
        }:
            raise ValueError(f"unsupported market_type: {self.market_type}")
        if self.side not in {"over", "under"}:
            raise ValueError("side must be over or under")
        if (
            self.reference_decimal_odds is not None
            and self.reference_decimal_odds <= 1
        ):
            raise ValueError("reference_decimal_odds must exceed one")
        if self.market_type == "player_prop":
            if not self.player_id:
                raise ValueError("player_prop requires player_id")
            if not self.stats:
                raise ValueError("player_prop requires at least one stat")


@dataclass(frozen=True)
class MarketOutput:
    run_id: str
    commit_sha: str
    market_id: str
    market_type: str
    player_id: str | None
    stats: tuple[str, ...]
    line: float
    pmf: Mapping[int, float]
    projected_mean: float
    projected_variance: float
    p_over: float
    p_push: float
    p_under: float
    fair_decimal_over: float
    fair_decimal_under: float
    offered_decimal_over: float | None
    offered_decimal_under: float | None
    max_stake_over: float
    max_stake_under: float
    quote_status: str
    quote_reasons: tuple[str, ...]
    monte_carlo_error: float
    time_decay_adjusted_edge: float | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["pmf"] = {
            str(value): probability
            for value, probability in self.pmf.items()
        }
        return result


@dataclass(frozen=True)
class ArtifactManifest:
    run_id: str
    commit_sha: str
    event_sequence: int
    expected_pmf_rows: int
    actual_pmf_rows: int
    duplicate_pmfs: int
    invalid_pmfs: int
    expected_market_rows: int
    actual_market_rows: int
    duplicate_market_rows: int
    stale_artifacts: int
    status: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationReport:
    metadata: RunMetadata
    markets: tuple[MarketOutput, ...]
    manifest: ArtifactManifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": asdict(self.metadata),
            "markets": [market.to_dict() for market in self.markets],
            "manifest": self.manifest.to_dict(),
        }
