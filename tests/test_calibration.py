from __future__ import annotations

import math
import pytest

from wizard_wnba.calibration import (
    BetaCalibrator,
    CalibrationError,
    CalibrationRegistry,
    UnsupportedMarketError,
)
from wizard_wnba.domain import ProbabilityEstimate


def estimate():
    return ProbabilityEstimate(
        market_key="player_points",
        line=20,
        p_win=.45,
        p_push=.10,
        p_loss=.45,
        fair_decimal_odds=2.0,
        projected_mean=20,
        projected_sd=4,
        simulation_count=1000,
        monte_carlo_se=.01,
        calibration_se=.01,
        model_se=.02,
        calibration_score=.9,
        calibrator_id="raw",
        model_version="v1",
        pmf={20: .10, 19: .45, 21: .45},
    )


def test_identity_calibration_preserves_probabilities_and_push():
    registry = CalibrationRegistry(
        {"player_points": BetaCalibrator("identity", 1, -1, 0)}
    )
    output = registry.apply(estimate(), key="player_points")
    assert math.isclose(output.p_win, .45, abs_tol=1e-9)
    assert output.p_push == .10
    assert output.calibrator_id == "identity"


def test_required_calibrator_fails_closed():
    with pytest.raises(CalibrationError):
        CalibrationRegistry({}).apply(
            estimate(),
            key="player_points",
            require=True,
        )


def test_missing_calibrator_raises_unsupported_market_never_none():
    # An unsupported market must raise a typed UnsupportedMarketError — never
    # return None (which would blow up on the caller's .validate()).
    registry = CalibrationRegistry(
        {"player_points": BetaCalibrator("identity", 1, -1, 0)}
    )
    with pytest.raises(UnsupportedMarketError) as excinfo:
        registry.calibrator_for("player_assists")
    assert excinfo.value.market_key == "player_assists"

    with pytest.raises(UnsupportedMarketError):
        registry.apply(estimate(), key="player_assists", require=True)


def test_unsupported_market_error_is_calibration_error_subclass():
    assert issubclass(UnsupportedMarketError, CalibrationError)


def test_eligibility_is_enforced_and_narrows_to_calibrated_markets():
    registry = CalibrationRegistry(
        {
            "player_points": BetaCalibrator("pp", 1, -1, 0),
            "h2h": BetaCalibrator("h2h", 1, -1, 0),
        },
        eligible_markets={"player_points", "player_assists"},
    )
    # Eligibility never exceeds what we can actually calibrate.
    assert registry.eligible_markets == {"player_points"}
    assert registry.is_eligible("player_points")
    assert not registry.is_eligible("h2h")
    assert not registry.is_eligible("player_assists")


def test_apply_returns_estimate_not_none_when_not_required():
    registry = CalibrationRegistry({})
    output = registry.apply(estimate(), key="player_assists", require=False)
    assert output is not None
    assert isinstance(output, ProbabilityEstimate)


def test_calibrator_for_returns_valid_calibrator():
    calibrator = BetaCalibrator("pp", 1, -1, 0)
    registry = CalibrationRegistry({"player_points": calibrator})
    assert registry.calibrator_for("player_points") is calibrator
