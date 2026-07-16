from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Iterable, Mapping

from .domain import ProbabilityEstimate
from .odds_math import fair_decimal_with_push


class CalibrationError(RuntimeError):
    pass


class UnsupportedMarketError(CalibrationError):
    """A market has no eligible production calibrator.

    Raised instead of ever returning ``None`` for a missing calibrator, so a
    caller can never invoke ``.validate()`` on a ``NoneType``. Callers that
    price a stream of markets catch this at the individual-market boundary and
    skip only that market, leaving supported markets unaffected.
    """

    def __init__(self, market_key: str) -> None:
        self.market_key = market_key
        super().__init__(
            f"market '{market_key}' has no eligible production calibrator"
        )


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
    def __init__(
        self,
        calibrators: Mapping[str, BetaCalibrator],
        *,
        eligible_markets: Iterable[str] | None = None,
    ) -> None:
        self.calibrators = dict(calibrators)
        # Eligibility defaults to the set of markets that actually have a
        # calibrator. An explicit set may be narrower (production policy) but
        # never wider than what we can calibrate.
        if eligible_markets is None:
            self.eligible_markets = frozenset(self.calibrators)
        else:
            self.eligible_markets = frozenset(eligible_markets) & frozenset(
                self.calibrators
            )

    def is_eligible(self, key: str) -> bool:
        return key in self.eligible_markets

    def calibrator_for(self, key: str) -> BetaCalibrator:
        """Return a valid calibrator or raise ``UnsupportedMarketError``.

        Never returns ``None`` — that is the whole point of the typed contract.
        """
        if key not in self.eligible_markets:
            raise UnsupportedMarketError(key)
        calibrator = self.calibrators.get(key)
        if calibrator is None:  # pragma: no cover - eligibility guards this
            raise UnsupportedMarketError(key)
        return calibrator

    def apply(
        self,
        estimate: ProbabilityEstimate,
        *,
        key: str,
        require: bool = True,
    ) -> ProbabilityEstimate:
        try:
            calibrator = self.calibrator_for(key)
        except UnsupportedMarketError:
            if require:
                raise
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
