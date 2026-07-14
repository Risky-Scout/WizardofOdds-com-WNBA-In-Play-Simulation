from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

from .domain import ProbabilityEstimate
from .odds_math import fair_decimal_with_push


class CalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BetaCalibrator:
    calibrator_id: str
    a: float
    b: float
    c: float

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("probability must be in [0,1]")
        clipped = min(max(probability, 1e-10), 1 - 1e-10)
        score = (
            self.a * math.log(clipped)
            + self.b * math.log(1 - clipped)
            + self.c
        )
        score = max(min(score, 40), -40)
        return 1 / (1 + math.exp(-score))


class CalibrationRegistry:
    def __init__(self, calibrators: Mapping[str, BetaCalibrator]) -> None:
        self.calibrators = dict(calibrators)

    def apply(
        self,
        estimate: ProbabilityEstimate,
        *,
        key: str,
        require: bool = True,
    ) -> ProbabilityEstimate:
        calibrator = self.calibrators.get(key)
        if calibrator is None:
            if require:
                raise CalibrationError(f"required calibrator missing for {key}")
            return estimate

        non_push = 1 - estimate.p_push
        if non_push <= 0:
            return replace(
                estimate,
                calibrator_id=calibrator.calibrator_id,
            )

        conditional_win = estimate.p_win / non_push
        calibrated_win = calibrator.transform(conditional_win)
        p_win = calibrated_win * non_push
        p_loss = (1 - calibrated_win) * non_push

        output = replace(
            estimate,
            p_win=p_win,
            p_loss=p_loss,
            fair_decimal_odds=fair_decimal_with_push(p_win, estimate.p_push),
            calibrator_id=calibrator.calibrator_id,
        )
        output.validate()
        return output
