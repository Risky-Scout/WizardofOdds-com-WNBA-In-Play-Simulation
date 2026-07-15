from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Protocol

from .simulation import OverPushUnder


class CalibrationError(RuntimeError):
    pass


class ProbabilityCalibrator(Protocol):
    calibrator_id: str

    def transform(self, probability: float) -> float:
        ...


@dataclass(frozen=True)
class BetaCalibrator:
    """Three-parameter beta calibration on a binary probability.

    Identity calibration is represented by a=1, b=-1, c=0.
    Parameters must be estimated using time-ordered, out-of-sample data.
    """

    calibrator_id: str
    a: float
    b: float
    c: float

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("probability must be in [0, 1]")
        clipped = min(max(probability, 1e-10), 1 - 1e-10)
        score = (
            self.a * math.log(clipped)
            + self.b * math.log(1 - clipped)
            + self.c
        )
        return 1 / (1 + math.exp(-max(min(score, 40), -40)))


@dataclass(frozen=True)
class CalibrationOutcome:
    probabilities: OverPushUnder
    applied: bool
    calibrator_id: str | None
    reason: str | None = None


class CalibrationRegistry:
    """Selects named calibrators and preserves integer-line push mass."""

    def __init__(
        self,
        calibrators: Mapping[str, ProbabilityCalibrator],
    ) -> None:
        self._calibrators = dict(calibrators)

    def apply(
        self,
        key: str,
        probabilities: OverPushUnder,
        require_calibration: bool = True,
    ) -> CalibrationOutcome:
        probabilities.validate()
        calibrator = self._calibrators.get(key)

        if calibrator is None:
            if require_calibration:
                raise CalibrationError(
                    f"required calibrator not found for key: {key}"
                )
            return CalibrationOutcome(
                probabilities=probabilities,
                applied=False,
                calibrator_id=None,
                reason="ALLOW_UNCALIBRATED",
            )

        non_push = 1 - probabilities.p_push
        if non_push <= 0:
            return CalibrationOutcome(
                probabilities=probabilities,
                applied=True,
                calibrator_id=calibrator.calibrator_id,
            )

        conditional_over = probabilities.p_over / non_push
        calibrated_over = calibrator.transform(conditional_over)
        calibrated = OverPushUnder(
            line=probabilities.line,
            p_over=calibrated_over * non_push,
            p_push=probabilities.p_push,
            p_under=(1 - calibrated_over) * non_push,
        )
        calibrated.validate()
        return CalibrationOutcome(
            probabilities=calibrated,
            applied=True,
            calibrator_id=calibrator.calibrator_id,
        )
