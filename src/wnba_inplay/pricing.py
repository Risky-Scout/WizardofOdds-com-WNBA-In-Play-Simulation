from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import fmean
from typing import Mapping, Sequence

from .simulation import OverPushUnder


@dataclass(frozen=True)
class CalibrationStatus:
    applied: bool
    calibrator_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class QuoteContext:
    base_margin: float
    base_limit: float
    exposure_over: float
    exposure_under: float
    risk_capacity: float
    input_age_ms: int
    max_input_age_ms: int
    model_uncertainty: float
    max_model_uncertainty: float
    monte_carlo_error: float
    max_monte_carlo_error: float
    calibration: CalibrationStatus
    require_calibration: bool = True
    event_sequence_gap: bool = False
    unresolved_review: bool = False
    pmf_valid: bool = True

    def validate(self) -> None:
        if self.base_margin < 0:
            raise ValueError("base_margin cannot be negative")
        if self.base_limit < 0:
            raise ValueError("base_limit cannot be negative")
        if self.risk_capacity <= 0:
            raise ValueError("risk_capacity must be positive")
        if self.max_input_age_ms <= 0:
            raise ValueError("max_input_age_ms must be positive")
        if self.max_model_uncertainty <= 0:
            raise ValueError("max_model_uncertainty must be positive")
        if self.max_monte_carlo_error <= 0:
            raise ValueError("max_monte_carlo_error must be positive")


@dataclass(frozen=True)
class SuspensionDecision:
    suspended: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TwoWayQuote:
    status: str
    reasons: tuple[str, ...]
    fair_probability_over: float
    fair_probability_push: float
    fair_probability_under: float
    fair_decimal_over: float
    fair_decimal_under: float
    offered_decimal_over: float | None
    offered_decimal_under: float | None
    offered_overround: float | None
    max_stake_over: float
    max_stake_under: float
    effective_margin: float | None


def fair_decimal_odds(probability_win: float, probability_push: float) -> float:
    if not 0 <= probability_win <= 1:
        raise ValueError("probability_win must be in [0, 1]")
    if not 0 <= probability_push <= 1:
        raise ValueError("probability_push must be in [0, 1]")
    if probability_win + probability_push > 1 + 1e-12:
        raise ValueError("win plus push probability cannot exceed one")
    if probability_win == 0:
        return math.inf
    return (1 - probability_push) / probability_win


class SuspensionPolicy:
    def evaluate(self, context: QuoteContext) -> SuspensionDecision:
        context.validate()
        reasons: list[str] = []

        if context.event_sequence_gap:
            reasons.append("EVENT_SEQUENCE_GAP")
        if context.unresolved_review:
            reasons.append("UNRESOLVED_REVIEW")
        if not context.pmf_valid:
            reasons.append("INVALID_PMF")
        if context.input_age_ms > context.max_input_age_ms:
            reasons.append("STALE_INPUT")
        if context.model_uncertainty > context.max_model_uncertainty:
            reasons.append("MODEL_UNCERTAINTY_LIMIT")
        if context.monte_carlo_error > context.max_monte_carlo_error:
            reasons.append("MONTE_CARLO_ERROR_LIMIT")
        if context.require_calibration and not context.calibration.applied:
            reasons.append("CALIBRATION_REQUIRED")

        return SuspensionDecision(
            suspended=bool(reasons),
            reasons=tuple(reasons),
        )


class QuoteOptimizer:
    """Transforms immutable fair probabilities into risk-adjusted quotes."""

    def __init__(
        self,
        suspension_policy: SuspensionPolicy | None = None,
    ) -> None:
        self.suspension_policy = suspension_policy or SuspensionPolicy()

    def quote(
        self,
        probabilities: OverPushUnder,
        context: QuoteContext,
    ) -> TwoWayQuote:
        probabilities.validate()
        decision = self.suspension_policy.evaluate(context)

        fair_over = fair_decimal_odds(
            probabilities.p_over,
            probabilities.p_push,
        )
        fair_under = fair_decimal_odds(
            probabilities.p_under,
            probabilities.p_push,
        )

        if decision.suspended:
            return TwoWayQuote(
                status="SUSPENDED",
                reasons=decision.reasons,
                fair_probability_over=probabilities.p_over,
                fair_probability_push=probabilities.p_push,
                fair_probability_under=probabilities.p_under,
                fair_decimal_over=fair_over,
                fair_decimal_under=fair_under,
                offered_decimal_over=None,
                offered_decimal_under=None,
                offered_overround=None,
                max_stake_over=0.0,
                max_stake_under=0.0,
                effective_margin=None,
            )

        non_push = 1 - probabilities.p_push
        if non_push <= 0:
            raise ValueError("market has no win/loss probability mass")

        conditional_over = probabilities.p_over / non_push
        conditional_under = probabilities.p_under / non_push

        uncertainty_ratio = min(
            context.model_uncertainty / context.max_model_uncertainty,
            1.0,
        )
        mc_ratio = min(
            context.monte_carlo_error / context.max_monte_carlo_error,
            1.0,
        )
        staleness_ratio = min(
            context.input_age_ms / context.max_input_age_ms,
            1.0,
        )

        effective_margin = (
            context.base_margin
            + 0.035 * uncertainty_ratio
            + 0.020 * mc_ratio
            + 0.020 * staleness_ratio
        )

        net_exposure = context.exposure_over - context.exposure_under
        exposure_ratio = max(
            min(net_exposure / context.risk_capacity, 2.0),
            -2.0,
        )
        # Positive over exposure shortens over odds and lengthens under odds.
        inventory_tilt = 0.20 * exposure_ratio

        log_over = math.log(max(conditional_over, 1e-12)) + inventory_tilt
        log_under = math.log(max(conditional_under, 1e-12)) - inventory_tilt
        normalizer = self._logsumexp(log_over, log_under)
        risk_over = math.exp(log_over - normalizer)
        risk_under = math.exp(log_under - normalizer)

        implied_over = risk_over * (1 + effective_margin)
        implied_under = risk_under * (1 + effective_margin)
        offered_over = 1 / implied_over
        offered_under = 1 / implied_under

        base_limit_multiplier = (
            max(0.05, (1 - uncertainty_ratio) ** 2)
            * max(0.10, 1 - 0.75 * staleness_ratio)
            * max(0.10, 1 - 0.65 * mc_ratio)
        )
        concentration = 1 + abs(net_exposure) / context.risk_capacity
        common_limit = context.base_limit * base_limit_multiplier / concentration

        # Further restrict the already-heavy side.
        over_side_factor = 1 / (1 + max(exposure_ratio, 0))
        under_side_factor = 1 / (1 + max(-exposure_ratio, 0))

        return TwoWayQuote(
            status="OPEN",
            reasons=(),
            fair_probability_over=probabilities.p_over,
            fair_probability_push=probabilities.p_push,
            fair_probability_under=probabilities.p_under,
            fair_decimal_over=fair_over,
            fair_decimal_under=fair_under,
            offered_decimal_over=offered_over,
            offered_decimal_under=offered_under,
            offered_overround=implied_over + implied_under,
            max_stake_over=common_limit * over_side_factor,
            max_stake_under=common_limit * under_side_factor,
            effective_margin=effective_margin,
        )

    @staticmethod
    def _logsumexp(first: float, second: float) -> float:
        maximum = max(first, second)
        return maximum + math.log(
            math.exp(first - maximum) + math.exp(second - maximum)
        )


@dataclass(frozen=True)
class Wager:
    market_id: str
    side: str
    line: float
    decimal_odds: float
    stake: float

    def validate(self) -> None:
        if self.side not in {"over", "under"}:
            raise ValueError("side must be 'over' or 'under'")
        if self.decimal_odds <= 1:
            raise ValueError("decimal_odds must be greater than one")
        if self.stake <= 0:
            raise ValueError("stake must be positive")


@dataclass(frozen=True)
class PortfolioRisk:
    expected_pnl: float
    pnl_variance: float
    value_at_risk_99: float
    conditional_value_at_risk_99: float
    minimum_pnl: float
    maximum_pnl: float
    scenario_pnl: tuple[float, ...]


class PortfolioRiskEngine:
    """Settles a portfolio over joint simulation paths from the fair engine."""

    def evaluate(
        self,
        wagers: Sequence[Wager],
        market_values: Mapping[str, Sequence[int]],
    ) -> PortfolioRisk:
        if not wagers:
            raise ValueError("wagers cannot be empty")
        for wager in wagers:
            wager.validate()
            if wager.market_id not in market_values:
                raise ValueError(f"missing scenarios for {wager.market_id}")

        scenario_count = len(market_values[wagers[0].market_id])
        if scenario_count <= 0:
            raise ValueError("market scenarios cannot be empty")
        if any(
            len(market_values[wager.market_id]) != scenario_count
            for wager in wagers
        ):
            raise ValueError("all markets must share the same scenario count")

        pnl = [0.0 for _ in range(scenario_count)]

        for wager in wagers:
            values = market_values[wager.market_id]
            for index, value in enumerate(values):
                if math.isclose(value, wager.line):
                    settlement = 0.0
                else:
                    wins = (
                        value > wager.line
                        if wager.side == "over"
                        else value < wager.line
                    )
                    settlement = (
                        -wager.stake * (wager.decimal_odds - 1)
                        if wins
                        else wager.stake
                    )
                pnl[index] += settlement

        expected = fmean(pnl)
        variance = fmean((value - expected) ** 2 for value in pnl)
        ordered = sorted(pnl)
        tail_count = max(1, math.ceil(0.01 * scenario_count))
        worst_tail = ordered[:tail_count]
        # Report loss magnitudes as positive risk numbers.
        var_99 = max(0.0, -ordered[tail_count - 1])
        cvar_99 = max(0.0, -fmean(worst_tail))

        return PortfolioRisk(
            expected_pnl=expected,
            pnl_variance=variance,
            value_at_risk_99=var_99,
            conditional_value_at_risk_99=cvar_99,
            minimum_pnl=min(pnl),
            maximum_pnl=max(pnl),
            scenario_pnl=tuple(pnl),
        )
