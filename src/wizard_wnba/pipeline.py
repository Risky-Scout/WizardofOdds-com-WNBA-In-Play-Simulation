from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .calibration import BetaCalibrator, CalibrationRegistry
from .consensus import build_consensus
from .domain import (
    GameState,
    MarketOffer,
    PlayerLiveState,
    PlayerRateProfile,
    ProbabilityEstimate,
    Recommendation,
)
from .recommendation import AdaptivePolicy
from .simulation import MonteCarloEngine, SimulationBundle


@dataclass(frozen=True)
class ModelMetadata:
    model_version: str
    calibrator_id: str
    calibration_score: float
    calibration_se: float
    model_se: float
    calibration_parameters: Mapping[str, tuple[float, float, float]] = field(
        default_factory=dict
    )


class RecommendationPipeline:
    def __init__(
        self,
        *,
        simulator: MonteCarloEngine | None = None,
        policy: AdaptivePolicy | None = None,
    ) -> None:
        self.simulator = simulator or MonteCarloEngine()
        self.policy = policy or AdaptivePolicy()

    def evaluate_game(
        self,
        *,
        game: GameState,
        live_players: Mapping[str, PlayerLiveState],
        profiles: Mapping[str, PlayerRateProfile],
        offers: Sequence[MarketOffer],
        model: ModelMetadata,
        simulations: int,
        seed: int,
    ) -> tuple[Recommendation, ...]:
        bundle = self.simulator.simulate(
            game=game,
            live_players=live_players,
            profiles=profiles,
            simulations=simulations,
            seed=seed,
        )
        recommendations: list[Recommendation] = []

        for offer in offers:
            if offer.canonical_game_id != game.canonical_game_id:
                continue
            if offer.line is None and offer.market_key != "h2h":
                continue
            if offer.side not in {"over", "under"}:
                continue

            probability = self._price_offer(
                offer=offer,
                bundle=bundle,
                model=model,
            )
            if probability is None:
                continue
            calibrators = {
                key: BetaCalibrator(
                    calibrator_id=f"{model.calibrator_id}:{key}",
                    a=parameters[0],
                    b=parameters[1],
                    c=parameters[2],
                )
                for key, parameters in model.calibration_parameters.items()
            }
            probability = CalibrationRegistry(calibrators).apply(
                probability,
                key=offer.market_key,
                require=True,
            )

            leave_one_out = build_consensus(
                offers,
                target_side=offer.side,
                exclude_bookmaker=offer.bookmaker_key,
            )
            key = (
                offer.canonical_game_id,
                offer.market_key,
                offer.canonical_player_id,
                offer.line,
            )
            consensus = leave_one_out.get(key)
            recommendations.append(
                self.policy.evaluate(
                    game=game,
                    offer=offer,
                    probability=probability,
                    consensus=consensus,
                )
            )

        return tuple(recommendations)

    def _price_offer(
        self,
        *,
        offer: MarketOffer,
        bundle: SimulationBundle,
        model: ModelMetadata,
    ) -> ProbabilityEstimate | None:
        line = offer.line if offer.line is not None else 0.5
        kwargs = {
            "bundle": bundle,
            "market_key": offer.market_key,
            "line": line,
            "side": offer.side,
            "calibration_score": model.calibration_score,
            "calibrator_id": model.calibrator_id,
            "model_version": model.model_version,
            "calibration_se": model.calibration_se,
            "model_se": model.model_se,
        }
        if offer.market_key.startswith("player_"):
            if not offer.canonical_player_id:
                return None
            return self.simulator.price_player_market(
                player_id=offer.canonical_player_id,
                **kwargs,
            )
        if offer.market_key in {"h2h", "spreads", "totals"}:
            return self.simulator.price_game_market(**kwargs)
        return None
