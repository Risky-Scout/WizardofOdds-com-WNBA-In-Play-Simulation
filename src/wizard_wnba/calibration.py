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


# --- Hierarchical (Bühlmann credibility) calibration ------------------------
#
# The legacy pipeline fits an independent beta calibrator per market and falls
# back HARD to the pooled ("__global__") calibrator when a market has < 100
# samples. That is a step function: a market with 99 samples gets 0% of its own
# fit, one with 100 gets 100%. Partial pooling replaces the step with smooth
# credibility shrinkage — each market's coefficients shrink toward the pooled
# coefficients with weight n / (n + k). Thin player-prop markets borrow strength
# from the pool; rich game-line markets keep their own fit. k is tuned by
# walk-forward cross-validation on held-out validation log-loss.
#
# This is a FITTING strategy only. Its output is the same [a, b, c] triple per
# market that the bundle already stores, so BetaCalibrator, CalibrationRegistry,
# and the bundle contract are all unchanged.

from typing import Sequence  # noqa: E402


def _solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot_row = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot_row][col]) < 1e-12:
            raise ValueError("singular calibration system")
        a[col], a[pivot_row] = a[pivot_row], a[col]
        pivot = a[col][col]
        a[col] = [value / pivot for value in a[col]]
        for r in range(3):
            if r != col:
                factor = a[r][col]
                a[r] = [value - factor * a[col][i] for i, value in enumerate(a[r])]
    return [a[i][3] for i in range(3)]


def fit_beta_coefficients(
    samples: Sequence[tuple[float, int, float]],
    *,
    ridge: float = 1e-4,
    max_iterations: int = 80,
    prior: tuple[float, float, float] = (1.0, -1.0, 0.0),
) -> tuple[float, float, float]:
    """IRLS fit of beta calibration coefficients (a, b, c) on
    features [log p, log(1-p), 1]. ``samples`` are (raw_prob, outcome, weight).
    Returns the identity-ish prior if there are too few samples to fit."""
    usable = [
        (min(max(float(p), 1e-8), 1 - 1e-8), int(y), max(0.0, float(w)))
        for p, y, w in samples
        if y in (0, 1)
    ]
    if len(usable) < 30:
        return prior

    coeffs = list(prior)
    for _ in range(max_iterations):
        hessian = [[0.0] * 3 for _ in range(3)]
        gradient = [0.0] * 3
        for p, y, w in usable:
            feats = [math.log(p), math.log(1 - p), 1.0]
            eta = max(min(sum(c * f for c, f in zip(coeffs, feats)), 35.0), -35.0)
            fitted = 1.0 / (1.0 + math.exp(-eta))
            residual = y - fitted
            variance = max(fitted * (1 - fitted), 1e-8)
            for i in range(3):
                gradient[i] += w * residual * feats[i]
                for j in range(3):
                    hessian[i][j] += w * variance * feats[i] * feats[j]
        for i in range(3):
            hessian[i][i] += ridge
        try:
            delta = _solve_3x3(hessian, gradient)
        except ValueError:
            break
        coeffs = [c + d for c, d in zip(coeffs, delta)]
        if max(abs(d) for d in delta) < 1e-8:
            break
    return (coeffs[0], coeffs[1], coeffs[2])


def _log_loss_beta(
    samples: Sequence[tuple[float, int, float]],
    coeffs: tuple[float, float, float],
) -> float | None:
    cal = BetaCalibrator("cv", *coeffs)
    total = weight = 0.0
    for p, y, w in samples:
        if y not in (0, 1):
            continue
        q = min(max(cal.transform(min(max(float(p), 1e-8), 1 - 1e-8)), 1e-12), 1 - 1e-12)
        total += w * -(y * math.log(q) + (1 - y) * math.log(1 - q))
        weight += w
    return total / weight if weight else None


@dataclass(frozen=True)
class HierarchicalBetaCalibrator:
    """A set of per-market beta calibrators partially pooled toward a pooled
    ("global") calibrator via Bühlmann credibility weight n / (n + k)."""

    pooled: tuple[float, float, float]
    own: Mapping[str, tuple[float, float, float]]
    sizes: Mapping[str, int]
    k: float
    calibrator_id: str = "hierarchical-buhlmann-v1"

    def credibility(self, market: str) -> float:
        n = int(self.sizes.get(market, 0))
        if self.k <= 0:
            return 1.0 if n > 0 else 0.0
        return n / (n + self.k)

    def shrunk_coefficients(self, market: str) -> tuple[float, float, float]:
        own = self.own.get(market, self.pooled)
        w = self.credibility(market)
        return tuple(w * o + (1.0 - w) * g for o, g in zip(own, self.pooled))  # type: ignore[return-value]

    def calibrator_for(self, market: str) -> BetaCalibrator:
        a, b, c = self.shrunk_coefficients(market)
        return BetaCalibrator(f"{self.calibrator_id}:{market}", a, b, c)

    def bundle_parameters(self) -> dict[str, list[float]]:
        """Shrunk [a, b, c] per market — drop-in for bundle calibration_parameters."""
        return {m: list(self.shrunk_coefficients(m)) for m in self.own}

    @classmethod
    def fit(
        cls,
        samples_by_market: Mapping[str, Sequence[tuple[float, int, float]]],
        *,
        k: float,
    ) -> "HierarchicalBetaCalibrator":
        pooled_samples = [s for rows in samples_by_market.values() for s in rows]
        pooled = fit_beta_coefficients(pooled_samples)
        own = {m: fit_beta_coefficients(rows) for m, rows in samples_by_market.items()}
        sizes = {m: sum(1 for _, y, _ in rows if y in (0, 1))
                 for m, rows in samples_by_market.items()}
        return cls(pooled=pooled, own=own, sizes=sizes, k=float(k))

    @staticmethod
    def tune_k(
        fit_by_market: Mapping[str, Sequence[tuple[float, int, float]]],
        validation_by_market: Mapping[str, Sequence[tuple[float, int, float]]],
        *,
        candidate_ks: Sequence[float] = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0),
    ) -> tuple[float, dict[float, float]]:
        """Pick k minimizing pooled validation log-loss (walk-forward CV).
        Returns (best_k, {k: val_log_loss})."""
        scores: dict[float, float] = {}
        for k in candidate_ks:
            model = HierarchicalBetaCalibrator.fit(fit_by_market, k=k)
            total = weight = 0.0
            for market, rows in validation_by_market.items():
                coeffs = model.shrunk_coefficients(market)
                ll = _log_loss_beta(rows, coeffs)
                if ll is None:
                    continue
                n = sum(1 for _, y, _ in rows if y in (0, 1))
                total += ll * n
                weight += n
            scores[k] = total / weight if weight else float("inf")
        best_k = min(scores, key=lambda kk: scores[kk])
        return best_k, scores
