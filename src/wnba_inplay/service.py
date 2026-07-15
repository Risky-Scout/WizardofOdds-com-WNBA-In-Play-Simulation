from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

from .contracts import (
    ArtifactManifest,
    MarketOutput,
    MarketSpec,
    RunMetadata,
    SimulationReport,
)
from .domain import GameState
from .pricing import QuoteContext, QuoteOptimizer
from .rotation import PlayerRotationProfile
from .simulation import (
    DiscretePMF,
    InPlaySimulator,
    PlayerEventProfile,
    SimulationPath,
    TeamSimulationProfile,
    combination_pmf,
    game_market_pmf,
    monte_carlo_standard_error,
)
from .validation import validate_outputs


@dataclass(frozen=True)
class SimulationRequest:
    state: GameState
    rotation_profiles: Mapping[str, PlayerRotationProfile]
    player_profiles: Mapping[str, PlayerEventProfile]
    team_profiles: Mapping[str, TeamSimulationProfile]
    markets: tuple[MarketSpec, ...]
    quote_contexts: Mapping[str, QuoteContext]
    metadata: RunMetadata
    simulations: int
    seed: int


class SimulationService:
    def __init__(
        self,
        simulator: InPlaySimulator | None = None,
        quote_optimizer: QuoteOptimizer | None = None,
    ) -> None:
        self.simulator = simulator or InPlaySimulator()
        self.quote_optimizer = quote_optimizer or QuoteOptimizer()

    def run(self, request: SimulationRequest) -> SimulationReport:
        request.metadata.validate()
        if request.state.sequence != request.metadata.event_sequence:
            raise ValueError("metadata event_sequence does not match state")
        if request.simulations <= 0:
            raise ValueError("simulations must be positive")

        for market in request.markets:
            market.validate()
            if market.market_id not in request.quote_contexts:
                raise ValueError(
                    f"missing quote context: {market.market_id}"
                )

        paths = self.simulator.simulate(
            request.state,
            request.rotation_profiles,
            request.player_profiles,
            request.team_profiles,
            simulations=request.simulations,
            seed=request.seed,
        )

        outputs = tuple(
            self._build_market_output(
                request,
                market,
                paths,
            )
            for market in request.markets
        )

        validation = validate_outputs(
            request.metadata,
            outputs,
            [market.market_id for market in request.markets],
        )
        reasons = validation.reasons
        manifest = ArtifactManifest(
            run_id=request.metadata.run_id,
            commit_sha=request.metadata.commit_sha,
            event_sequence=request.metadata.event_sequence,
            expected_pmf_rows=len(request.markets),
            actual_pmf_rows=len(outputs),
            duplicate_pmfs=validation.duplicate_pmfs,
            invalid_pmfs=validation.invalid_pmfs,
            expected_market_rows=len(request.markets),
            actual_market_rows=len(outputs),
            duplicate_market_rows=validation.duplicate_market_rows,
            stale_artifacts=validation.stale_artifacts,
            status="SUCCESS" if validation.valid else "FAILURE",
            reasons=reasons,
        )
        return SimulationReport(
            metadata=request.metadata,
            markets=outputs,
            manifest=manifest,
        )

    def _build_market_output(
        self,
        request: SimulationRequest,
        market: MarketSpec,
        paths: Sequence[SimulationPath],
    ) -> MarketOutput:
        pmf = self._market_pmf(market, paths)
        opu = pmf.over_push_under(market.line)
        selected_probability = (
            opu.p_over if market.side == "over" else opu.p_under
        )
        mc_error = monte_carlo_standard_error(
            selected_probability,
            request.simulations,
        )
        quote_context = replace(
            request.quote_contexts[market.market_id],
            monte_carlo_error=mc_error,
        )
        quote = self.quote_optimizer.quote(opu, quote_context)

        # Compare fair conditional probability with an external reference
        # price. This is a time-decayed model edge, not closing-line value.
        time_decay_adjusted_edge = None
        if market.reference_decimal_odds is not None:
            implied = 1 / market.reference_decimal_odds
            conditional_fair = selected_probability / max(1 - opu.p_push, 1e-12)
            decay = math.exp(
                -quote_context.input_age_ms
                / max(quote_context.max_input_age_ms, 1)
            )
            time_decay_adjusted_edge = (conditional_fair - implied) * decay

        return MarketOutput(
            run_id=request.metadata.run_id,
            commit_sha=request.metadata.commit_sha,
            market_id=market.market_id,
            market_type=market.market_type,
            player_id=market.player_id,
            stats=market.stats,
            line=market.line,
            pmf=pmf.probabilities,
            projected_mean=pmf.mean(),
            projected_variance=pmf.variance(),
            p_over=opu.p_over,
            p_push=opu.p_push,
            p_under=opu.p_under,
            fair_decimal_over=quote.fair_decimal_over,
            fair_decimal_under=quote.fair_decimal_under,
            offered_decimal_over=quote.offered_decimal_over,
            offered_decimal_under=quote.offered_decimal_under,
            max_stake_over=quote.max_stake_over,
            max_stake_under=quote.max_stake_under,
            quote_status=quote.status,
            quote_reasons=quote.reasons,
            monte_carlo_error=mc_error,
            time_decay_adjusted_edge=time_decay_adjusted_edge,
        )

    @staticmethod
    def _market_pmf(
        market: MarketSpec,
        paths: Sequence[SimulationPath],
    ) -> DiscretePMF:
        if market.market_type == "player_prop":
            assert market.player_id is not None
            return combination_pmf(
                paths,
                market.player_id,
                market.stats,
            )
        if market.market_type == "game_total":
            return game_market_pmf(paths, lambda path: path.game_total)
        if market.market_type == "home_spread":
            return game_market_pmf(paths, lambda path: path.home_margin)
        if market.market_type == "moneyline_home":
            return game_market_pmf(
                paths,
                lambda path: 1 if path.home_score > path.away_score else 0,
            )
        raise ValueError(f"unsupported market_type: {market.market_type}")
