from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

from .odds_math import (
    american_to_decimal,
    decimal_to_american,
    expected_roi,
    fair_decimal_with_push,
)


class ScenarioRequest(BaseModel):
    recommendation_id: str
    side: str = Field(pattern="^(over|under)$")
    line: float
    american_odds: int
    current_stat: float | None = Field(default=None, ge=0)
    baseline_remaining_minutes: float | None = Field(
        default=None,
        gt=0,
        le=60,
    )
    remaining_minutes: float | None = Field(default=None, ge=0, le=60)
    uncertainty_multiplier: float = Field(default=1.0, ge=0.5, le=3.0)

    @field_validator("american_odds")
    @classmethod
    def validate_american_odds(cls, value: int) -> int:
        if value == 0 or -100 < value < 100:
            raise ValueError(
                "American odds must be <= -100 or >= +100"
            )
        return value


@dataclass(frozen=True)
class ScenarioResult:
    recommendation_id: str
    official_recommendation: bool
    odds_format: str
    side: str
    line: float
    american_odds: int
    fair_american_odds: int | None
    adjusted_projected_mean: float
    adjusted_projected_sd: float
    p_win: float
    p_push: float
    p_loss: float
    expected_roi: float
    conservative_roi: float
    total_uncertainty: float
    distribution: tuple[dict[str, float | int], ...]
    distribution_source: str
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "official_recommendation": self.official_recommendation,
            "odds_format": self.odds_format,
            "side": self.side,
            "line": self.line,
            "american_odds": self.american_odds,
            "fair_american_odds": self.fair_american_odds,
            "adjusted_projected_mean": self.adjusted_projected_mean,
            "adjusted_projected_sd": self.adjusted_projected_sd,
            "p_win": self.p_win,
            "p_push": self.p_push,
            "p_loss": self.p_loss,
            "expected_roi": self.expected_roi,
            "conservative_roi": self.conservative_roi,
            "total_uncertainty": self.total_uncertainty,
            "distribution": list(self.distribution),
            "distribution_source": self.distribution_source,
            "disclaimer": self.disclaimer,
        }


def _normal_cdf(value: float, mean: float, sd: float) -> float:
    return 0.5 * (
        1.0 + math.erf((value - mean) / (sd * math.sqrt(2.0)))
    )


def _discrete_normal_pmf(
    mean: float,
    sd: float,
    *,
    allow_negative: bool,
) -> dict[int, float]:
    sd = max(sd, 0.50)
    lower = math.floor(mean - 6.0 * sd)
    upper = math.ceil(mean + 6.0 * sd)

    if not allow_negative:
        lower = max(0, lower)
    if upper - lower > 180:
        lower = math.floor(mean - 90)
        upper = math.ceil(mean + 90)

    probabilities: dict[int, float] = {}
    for value in range(lower, upper + 1):
        probability = (
            _normal_cdf(value + 0.5, mean, sd)
            - _normal_cdf(value - 0.5, mean, sd)
        )
        probabilities[value] = max(probability, 0.0)

    total = sum(probabilities.values())
    if total <= 0:
        nearest = round(mean)
        return {nearest: 1.0}

    return {
        value: probability / total
        for value, probability in probabilities.items()
    }


def _parse_exact_pmf(
    recommendation: Mapping[str, Any],
) -> dict[int, float]:
    raw = recommendation.get("pmf")
    if not isinstance(raw, Mapping) or not raw:
        return {}

    parsed: dict[int, float] = {}
    for raw_value, raw_probability in raw.items():
        try:
            value = int(raw_value)
            probability = float(raw_probability)
        except (TypeError, ValueError):
            return {}

        if not math.isfinite(probability) or probability < 0:
            return {}
        parsed[value] = parsed.get(value, 0.0) + probability

    total = sum(parsed.values())
    if total <= 0:
        return {}

    return {
        value: probability / total
        for value, probability in parsed.items()
    }


def _validate_minutes_inputs(request: ScenarioRequest) -> bool:
    supplied = (
        request.current_stat is not None,
        request.baseline_remaining_minutes is not None,
        request.remaining_minutes is not None,
    )
    if any(supplied) and not all(supplied):
        raise ValueError(
            "current_stat, baseline_remaining_minutes, and "
            "remaining_minutes must be supplied together"
        )
    return all(supplied)


def _transform_exact_pmf_for_minutes(
    pmf: Mapping[int, float],
    request: ScenarioRequest,
) -> dict[int, float]:
    if not _validate_minutes_inputs(request):
        return dict(pmf)

    assert request.current_stat is not None
    assert request.baseline_remaining_minutes is not None
    assert request.remaining_minutes is not None

    ratio = (
        request.remaining_minutes
        / request.baseline_remaining_minutes
    )
    transformed: dict[int, float] = {}

    for final_value, probability in pmf.items():
        remaining_value = max(
            float(final_value) - request.current_stat,
            0.0,
        )
        adjusted = round(
            request.current_stat + remaining_value * ratio
        )
        transformed[int(adjusted)] = (
            transformed.get(int(adjusted), 0.0) + probability
        )

    total = sum(transformed.values())
    return {
        value: probability / total
        for value, probability in transformed.items()
    }


def _mean_and_sd(pmf: Mapping[int, float]) -> tuple[float, float]:
    mean = sum(value * probability for value, probability in pmf.items())
    variance = sum(
        (value - mean) ** 2 * probability
        for value, probability in pmf.items()
    )
    return mean, math.sqrt(max(variance, 0.0))


def _scenario_distribution(
    recommendation: Mapping[str, Any],
    request: ScenarioRequest,
) -> tuple[dict[int, float], str]:
    exact_pmf = _parse_exact_pmf(recommendation)
    if exact_pmf:
        return (
            _transform_exact_pmf_for_minutes(exact_pmf, request),
            "exact_simulation_pmf",
        )

    _validate_minutes_inputs(request)
    mean = float(recommendation["projected_mean"])
    sd = max(float(recommendation["projected_sd"]), 0.50)

    if request.current_stat is not None:
        assert request.baseline_remaining_minutes is not None
        assert request.remaining_minutes is not None
        remaining_mean = max(mean - request.current_stat, 0.0)
        ratio = (
            request.remaining_minutes
            / request.baseline_remaining_minutes
        )
        mean = request.current_stat + remaining_mean * ratio
        sd = max(0.50, sd * math.sqrt(max(ratio, 0.05)))

    allow_negative = (
        str(recommendation.get("market_key", "")) == "spreads"
    )
    return (
        _discrete_normal_pmf(
            mean,
            sd,
            allow_negative=allow_negative,
        ),
        "distributional_fallback",
    )


def run_scenario(
    recommendation: Mapping[str, Any],
    request: ScenarioRequest,
    *,
    confidence_z: float = 1.645,
) -> ScenarioResult:
    pmf, distribution_source = _scenario_distribution(
        recommendation,
        request,
    )
    mean, sd = _mean_and_sd(pmf)

    if request.side == "over":
        p_win = sum(
            probability
            for value, probability in pmf.items()
            if value > request.line
        )
        p_loss = sum(
            probability
            for value, probability in pmf.items()
            if value < request.line
        )
    else:
        p_win = sum(
            probability
            for value, probability in pmf.items()
            if value < request.line
        )
        p_loss = sum(
            probability
            for value, probability in pmf.items()
            if value > request.line
        )

    p_push = sum(
        probability
        for value, probability in pmf.items()
        if math.isclose(value, request.line)
    )

    normalization = p_win + p_push + p_loss
    p_win /= normalization
    p_push /= normalization
    p_loss /= normalization

    decimal_odds = american_to_decimal(request.american_odds)
    roi = expected_roi(
        p_win,
        p_push,
        p_loss,
        decimal_odds,
    )

    base_uncertainty = float(
        recommendation.get("total_uncertainty", 0.05)
    )
    total_uncertainty = min(
        0.25,
        base_uncertainty * request.uncertainty_multiplier,
    )
    lower_win = max(
        0.0,
        p_win - confidence_z * total_uncertainty,
    )
    upper_loss = min(
        1.0 - p_push,
        p_loss + confidence_z * total_uncertainty,
    )
    conservative_roi = (
        lower_win * (decimal_odds - 1.0) - upper_loss
    )

    fair_decimal = fair_decimal_with_push(p_win, p_push)
    fair_american = (
        None
        if not math.isfinite(fair_decimal) or fair_decimal <= 1.0
        else decimal_to_american(fair_decimal)
    )

    display_distribution = tuple(
        {
            "value": value,
            "probability": probability,
        }
        for value, probability in sorted(pmf.items())
        if probability >= 0.00025
    )

    return ScenarioResult(
        recommendation_id=request.recommendation_id,
        official_recommendation=False,
        odds_format="american",
        side=request.side,
        line=request.line,
        american_odds=request.american_odds,
        fair_american_odds=fair_american,
        adjusted_projected_mean=mean,
        adjusted_projected_sd=sd,
        p_win=p_win,
        p_push=p_push,
        p_loss=p_loss,
        expected_roi=roi,
        conservative_roi=conservative_roi,
        total_uncertainty=total_uncertainty,
        distribution=display_distribution,
        distribution_source=distribution_source,
        disclaimer=(
            "User-defined scenario. This result is not an official "
            "WizardofOdds.com published recommendation."
        ),
    )
