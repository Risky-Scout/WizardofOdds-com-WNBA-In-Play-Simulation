from __future__ import annotations

import math

from wnba_inplay.pricing import (
    CalibrationStatus,
    PortfolioRiskEngine,
    QuoteContext,
    QuoteOptimizer,
    Wager,
    fair_decimal_odds,
)
from wnba_inplay.simulation import OverPushUnder


def context(**overrides):
    values = {
        "base_margin": 0.04,
        "base_limit": 1000.0,
        "exposure_over": 0.0,
        "exposure_under": 0.0,
        "risk_capacity": 10000.0,
        "input_age_ms": 100,
        "max_input_age_ms": 2000,
        "model_uncertainty": 0.02,
        "max_model_uncertainty": 0.10,
        "monte_carlo_error": 0.005,
        "max_monte_carlo_error": 0.02,
        "calibration": CalibrationStatus(True, "cal-v1"),
    }
    values.update(overrides)
    return QuoteContext(**values)


def test_fair_odds_include_push_probability():
    assert math.isclose(fair_decimal_odds(0.45, 0.10), 2.0)


def test_quote_preserves_fair_probabilities_and_adds_overround():
    probabilities = OverPushUnder(20, 0.45, 0.10, 0.45)
    quote = QuoteOptimizer().quote(probabilities, context())
    assert quote.status == "OPEN"
    assert quote.fair_probability_over == 0.45
    assert quote.fair_probability_push == 0.10
    assert quote.fair_probability_under == 0.45
    assert quote.offered_overround is not None
    assert quote.effective_margin is not None
    assert math.isclose(
        quote.offered_overround,
        1 + quote.effective_margin,
        abs_tol=1e-12,
    )


def test_over_exposure_shortens_over_odds_and_reduces_over_limit():
    probabilities = OverPushUnder(20.5, 0.50, 0.0, 0.50)
    neutral = QuoteOptimizer().quote(probabilities, context())
    exposed = QuoteOptimizer().quote(
        probabilities,
        context(exposure_over=8000.0),
    )
    assert exposed.offered_decimal_over < neutral.offered_decimal_over
    assert exposed.offered_decimal_under > neutral.offered_decimal_under
    assert exposed.max_stake_over < exposed.max_stake_under


def test_stale_input_suspends_market():
    quote = QuoteOptimizer().quote(
        OverPushUnder(20.5, 0.5, 0.0, 0.5),
        context(input_age_ms=2500),
    )
    assert quote.status == "SUSPENDED"
    assert "STALE_INPUT" in quote.reasons
    assert quote.offered_decimal_over is None


def test_required_missing_calibration_suspends_market():
    quote = QuoteOptimizer().quote(
        OverPushUnder(20.5, 0.5, 0.0, 0.5),
        context(calibration=CalibrationStatus(False, reason="none applied")),
    )
    assert quote.status == "SUSPENDED"
    assert "CALIBRATION_REQUIRED" in quote.reasons


def test_explicit_uncalibrated_mode_may_continue():
    quote = QuoteOptimizer().quote(
        OverPushUnder(20.5, 0.5, 0.0, 0.5),
        context(
            calibration=CalibrationStatus(False, reason="shadow mode"),
            require_calibration=False,
        ),
    )
    assert quote.status == "OPEN"


def test_invalid_pmf_status_suspends_market():
    quote = QuoteOptimizer().quote(
        OverPushUnder(20.5, 0.5, 0.0, 0.5),
        context(pmf_valid=False),
    )
    assert quote.status == "SUSPENDED"
    assert "INVALID_PMF" in quote.reasons


def test_portfolio_settlement_handles_wins_losses_and_pushes():
    wagers = [
        Wager("points", "over", 20, 2.0, 100),
        Wager("total", "under", 150.5, 1.9, 50),
    ]
    values = {
        "points": [21, 20, 19],
        "total": [149, 151, 149],
    }
    risk = PortfolioRiskEngine().evaluate(wagers, values)
    assert risk.scenario_pnl == (-145.0, 50.0, 55.00000000000001)
    assert math.isclose(risk.expected_pnl, -40.0 / 3.0)


def test_portfolio_uses_joint_scenarios_not_independent_marginals():
    wagers = [
        Wager("player_points", "over", 20.5, 2.0, 100),
        Wager("game_total", "over", 150.5, 2.0, 100),
    ]
    aligned = {
        "player_points": [25, 15],
        "game_total": [160, 140],
    }
    risk = PortfolioRiskEngine().evaluate(wagers, aligned)
    assert risk.scenario_pnl == (-200.0, 200.0)
    assert risk.pnl_variance == 40000.0
    assert risk.conditional_value_at_risk_99 == 200.0
