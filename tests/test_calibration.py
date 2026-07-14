from __future__ import annotations

import math
import pytest

from wizard_wnba.calibration import (
    BetaCalibrator,
    CalibrationError,
    CalibrationRegistry,
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
