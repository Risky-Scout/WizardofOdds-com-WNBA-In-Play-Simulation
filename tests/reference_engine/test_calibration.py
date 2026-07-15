from __future__ import annotations

import math
import pytest

from wnba_inplay.calibration import (
    BetaCalibrator,
    CalibrationError,
    CalibrationRegistry,
)
from wnba_inplay.simulation import OverPushUnder


def test_identity_beta_calibrator_preserves_probability():
    calibrator = BetaCalibrator("identity", a=1, b=-1, c=0)
    assert math.isclose(calibrator.transform(0.37), 0.37, abs_tol=1e-10)


def test_calibration_preserves_push_mass_and_normalization():
    registry = CalibrationRegistry(
        {"points-q4": BetaCalibrator("points-q4-v1", 1.1, -0.9, -0.05)}
    )
    source = OverPushUnder(20, 0.42, 0.10, 0.48)
    result = registry.apply("points-q4", source)
    assert result.applied
    assert result.probabilities.p_push == 0.10
    assert math.isclose(
        result.probabilities.p_over
        + result.probabilities.p_push
        + result.probabilities.p_under,
        1.0,
    )


def test_required_calibrator_fails_closed():
    registry = CalibrationRegistry({})
    with pytest.raises(CalibrationError, match="required calibrator"):
        registry.apply(
            "missing",
            OverPushUnder(20.5, 0.5, 0.0, 0.5),
            require_calibration=True,
        )


def test_allow_uncalibrated_is_explicit():
    source = OverPushUnder(20.5, 0.5, 0.0, 0.5)
    result = CalibrationRegistry({}).apply(
        "missing",
        source,
        require_calibration=False,
    )
    assert not result.applied
    assert result.reason == "ALLOW_UNCALIBRATED"
    assert result.probabilities == source
