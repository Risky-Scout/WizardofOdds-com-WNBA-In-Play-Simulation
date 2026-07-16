from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import random
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ValidationThresholds:
    minimum_rows: int = 1000
    minimum_selected_bets: int = 200
    calibration_slope_min: float = 0.90
    calibration_slope_max: float = 1.10
    maximum_absolute_intercept: float = 0.10
    maximum_brier_gap_to_consensus: float = 0.002
    minimum_after_vig_roi: float = 0.0
    minimum_bootstrap_lower_roi: float = 0.0
    maximum_stale_residual_bias: float = 0.01
    maximum_availability_residual_bias: float = 0.01
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260715


@dataclass(frozen=True)
class OOSMetrics:
    sample_size: int
    selected_bets: int
    game_count: int
    calibration_intercept: float
    calibration_slope: float
    oos_brier: float
    consensus_brier: float | None
    brier_gap_to_consensus: float | None
    after_vig_roi: float | None
    bootstrap_roi_lower_95: float | None
    stale_residual_bias: float | None
    availability_residual_bias: float | None
    stale_bias_measurable: bool
    availability_bias_measurable: bool
    passed: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip_probability(value: float) -> float:
    return min(max(float(value), 1e-8), 1 - 1e-8)


def _logit(value: float) -> float:
    p = _clip_probability(value)
    return math.log(p / (1 - p))


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must have positive total")
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def _fit_logistic_calibration(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    weights: Sequence[float],
) -> tuple[float, float]:
    if len(probabilities) != len(outcomes) or len(probabilities) != len(weights):
        raise ValueError("calibration arrays must have equal length")
    if len(probabilities) < 20:
        raise ValueError("at least 20 rows are required for calibration")

    intercept = 0.0
    slope = 1.0
    ridge = 1e-8

    for _ in range(100):
        g0 = 0.0
        g1 = 0.0
        h00 = ridge
        h01 = 0.0
        h11 = ridge

        for probability, outcome, weight in zip(
            probabilities,
            outcomes,
            weights,
            strict=True,
        ):
            x = _logit(probability)
            eta = max(min(intercept + slope * x, 35.0), -35.0)
            fitted = 1.0 / (1.0 + math.exp(-eta))
            variance = max(fitted * (1.0 - fitted), 1e-12)
            residual = float(outcome) - fitted

            g0 += weight * residual
            g1 += weight * residual * x
            h00 += weight * variance
            h01 += weight * variance * x
            h11 += weight * variance * x * x

        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-14:
            break

        delta_intercept = (g0 * h11 - g1 * h01) / determinant
        delta_slope = (g1 * h00 - g0 * h01) / determinant

        intercept += delta_intercept
        slope += delta_slope

        if max(abs(delta_intercept), abs(delta_slope)) < 1e-9:
            break

    return intercept, slope


def _residual_bias(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> tuple[float | None, bool]:
    positive: list[float] = []
    negative: list[float] = []

    for row in rows:
        if field not in row:
            continue
        value = row.get(field)
        if value is None:
            continue
        residual = float(row["outcome"]) - float(row["probability"])
        (positive if bool(value) else negative).append(residual)

    if len(positive) < 30 or len(negative) < 30:
        return None, False

    return abs(mean(positive) - mean(negative)), True


def _bet_return(row: Mapping[str, Any]) -> float:
    result = str(row.get("result") or "").lower()
    if result == "push":
        return 0.0
    if result == "win":
        return float(row["offered_decimal_odds"]) - 1.0
    if result == "loss":
        return -1.0
    outcome = int(row["outcome"])
    return float(row["offered_decimal_odds"]) - 1.0 if outcome == 1 else -1.0


def _cluster_bootstrap_lower(
    rows: Sequence[Mapping[str, Any]],
    *,
    samples: int,
    seed: int,
) -> float | None:
    selected = [
        row
        for row in rows
        if bool(row.get("selected", False))
        and row.get("offered_decimal_odds") is not None
    ]
    if not selected:
        return None

    clusters: dict[str, list[float]] = {}
    for index, row in enumerate(selected):
        game_id = str(row.get("game_id") or f"row-{index}")
        clusters.setdefault(game_id, []).append(_bet_return(row))

    keys = sorted(clusters)
    if len(keys) < 20:
        return None

    rng = random.Random(seed)
    estimates: list[float] = []

    for _ in range(samples):
        sampled_returns: list[float] = []
        for _ in keys:
            key = keys[rng.randrange(len(keys))]
            sampled_returns.extend(clusters[key])
        estimates.append(mean(sampled_returns))

    estimates.sort()
    index = max(0, min(len(estimates) - 1, int(0.025 * len(estimates))))
    return estimates[index]


def evaluate_oos_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    thresholds: ValidationThresholds | None = None,
) -> OOSMetrics:
    gate = thresholds or ValidationThresholds()
    settled = [
        dict(row)
        for row in rows
        if row.get("probability") is not None
        and row.get("outcome") in {0, 1}
    ]

    if not settled:
        raise ValueError("no settled binary evaluation rows were supplied")

    probabilities = [_clip_probability(float(row["probability"])) for row in settled]
    outcomes = [int(row["outcome"]) for row in settled]
    weights = [max(float(row.get("weight", 1.0)), 0.0) for row in settled]

    intercept, slope = _fit_logistic_calibration(probabilities, outcomes, weights)
    brier = _weighted_mean(
        [(probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes, strict=True)],
        weights,
    )

    consensus_rows = [
        row
        for row in settled
        if row.get("consensus_probability") is not None
    ]
    consensus_brier = None
    brier_gap = None
    if len(consensus_rows) >= max(100, len(settled) // 2):
        consensus_brier = mean(
            (
                _clip_probability(float(row["consensus_probability"]))
                - int(row["outcome"])
            ) ** 2
            for row in consensus_rows
        )
        brier_gap = brier - consensus_brier

    selected_rows = [
        row
        for row in settled
        if bool(row.get("selected", False))
        and row.get("offered_decimal_odds") is not None
    ]
    roi = mean(_bet_return(row) for row in selected_rows) if selected_rows else None
    bootstrap_lower = _cluster_bootstrap_lower(
        settled,
        samples=gate.bootstrap_samples,
        seed=gate.bootstrap_seed,
    )

    stale_bias, stale_measurable = _residual_bias(settled, "stale")
    availability_bias, availability_measurable = _residual_bias(
        settled,
        "available",
    )

    blockers: list[str] = []
    if len(settled) < gate.minimum_rows:
        blockers.append("sample_size_below_minimum")
    if len(selected_rows) < gate.minimum_selected_bets:
        blockers.append("selected_bets_below_minimum")
    if not gate.calibration_slope_min <= slope <= gate.calibration_slope_max:
        blockers.append("calibration_slope_outside_gate")
    if abs(intercept) > gate.maximum_absolute_intercept:
        blockers.append("calibration_intercept_outside_gate")
    if brier_gap is None:
        blockers.append("consensus_brier_not_measurable")
    elif brier_gap > gate.maximum_brier_gap_to_consensus:
        blockers.append("brier_not_competitive_with_consensus")
    if roi is None or roi <= gate.minimum_after_vig_roi:
        blockers.append("after_vig_roi_not_positive")
    if bootstrap_lower is None or bootstrap_lower <= gate.minimum_bootstrap_lower_roi:
        blockers.append("bootstrap_lower_roi_not_positive")
    if not stale_measurable:
        blockers.append("stale_bias_not_measurable")
    elif stale_bias is not None and stale_bias > gate.maximum_stale_residual_bias:
        blockers.append("material_stale_line_bias")
    if not availability_measurable:
        blockers.append("availability_bias_not_measurable")
    elif (
        availability_bias is not None
        and availability_bias > gate.maximum_availability_residual_bias
    ):
        blockers.append("material_availability_bias")

    return OOSMetrics(
        sample_size=len(settled),
        selected_bets=len(selected_rows),
        game_count=len({str(row.get("game_id") or "") for row in settled}),
        calibration_intercept=intercept,
        calibration_slope=slope,
        oos_brier=brier,
        consensus_brier=consensus_brier,
        brier_gap_to_consensus=brier_gap,
        after_vig_roi=roi,
        bootstrap_roi_lower_95=bootstrap_lower,
        stale_residual_bias=stale_bias,
        availability_residual_bias=availability_bias,
        stale_bias_measurable=stale_measurable,
        availability_bias_measurable=availability_measurable,
        passed=not blockers,
        blockers=tuple(blockers),
    )


ELIGIBLE_MARKETS: tuple[str, ...] = (
    "h2h",
    "spreads",
    "totals",
    "player_points",
    "player_rebounds",
    "player_threes",
)


def prepare_oos_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    markets: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Canonical evaluation row set, shared by the pooled and per-market gates.

    A row is included iff it is settled (binary outcome), priced on an eligible
    production market, and flagged ``market_eligible``. Stale and unavailable
    rows are deliberately KEPT — the stale/availability bias gates need both
    populations to measure a difference. This is the single row-preparation
    both ``evaluate_oos_rows`` (pooled) and ``evaluate_by_market`` consume, so
    the two gates can never disagree because of a different input set.
    """
    allowed = set(markets if markets is not None else ELIGIBLE_MARKETS)
    prepared: list[dict[str, Any]] = []
    for row in rows:
        if row.get("probability") is None or row.get("outcome") not in {0, 1}:
            continue
        if str(row.get("market_key")) not in allowed:
            continue
        if "market_eligible" in row and not row.get("market_eligible"):
            continue
        prepared.append(dict(row))
    return prepared


@dataclass(frozen=True)
class PerMarketGate:
    """Per-market promotion gate. STRICTER than the pooled gate: every market
    must independently clear each threshold, so a weak market can no longer
    free-ride on the pooled sample. The pooled gate requires 200 selected bets
    once across all markets; here each market needs its own >=100, its own
    positive bootstrap lower bound, and its own in-band calibration slope."""

    minimum_selected_bets: int = 100
    minimum_bootstrap_lower_roi: float = 0.0
    calibration_slope_min: float = 0.85
    calibration_slope_max: float = 1.15
    maximum_stale_residual_bias: float = 0.01
    maximum_availability_residual_bias: float = 0.01
    bootstrap_samples: int = 5000
    bootstrap_seed: int = 20260715


@dataclass(frozen=True)
class MarketGateResult:
    market: str
    sample_size: int
    selected_bets: int
    game_count: int
    calibration_slope: float | None
    calibration_intercept: float | None
    brier: float | None
    log_loss: float | None
    consensus_brier: float | None
    brier_gap_to_consensus: float | None
    after_vig_roi: float | None
    bootstrap_roi_lower_95: float | None
    clv_mean: float | None
    clv_sample: int
    stale_residual_bias: float | None
    availability_residual_bias: float | None
    stale_bias_measurable: bool
    availability_bias_measurable: bool
    passed: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _log_loss(rows: Sequence[Mapping[str, Any]]) -> float | None:
    if not rows:
        return None
    total = 0.0
    for row in rows:
        p = _clip_probability(float(row["probability"]))
        y = int(row["outcome"])
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def _clv_for_selected(rows: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    """Closing-line-value proxy for the selected bets.

    True closing lines are not archived for these in-play checkpoints, so CLV
    is measured against the no-vig multi-book consensus at the checkpoint (the
    sharpest reference we have): CLV = consensus_prob(side) - implied_prob(price).
    Positive CLV means the bet was struck at a better price than the consensus
    fair value. Computable only where a >=2-book consensus exists.
    """
    values: list[float] = []
    for row in rows:
        if not bool(row.get("selected", False)):
            continue
        cons = row.get("consensus_probability")
        dec = row.get("offered_decimal_odds")
        if cons is None or dec is None:
            continue
        try:
            implied = 1.0 / float(dec)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        values.append(float(cons) - implied)
    if not values:
        return None, 0
    return mean(values), len(values)


def evaluate_market(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    gate: PerMarketGate | None = None,
) -> MarketGateResult:
    """Run the per-market gate on one market's already-prepared rows."""
    g = gate or PerMarketGate()
    settled = [dict(r) for r in rows if r.get("probability") is not None
               and r.get("outcome") in {0, 1}]

    if not settled:
        return MarketGateResult(
            market=market, sample_size=0, selected_bets=0, game_count=0,
            calibration_slope=None, calibration_intercept=None, brier=None,
            log_loss=None, consensus_brier=None, brier_gap_to_consensus=None,
            after_vig_roi=None, bootstrap_roi_lower_95=None, clv_mean=None,
            clv_sample=0, stale_residual_bias=None, availability_residual_bias=None,
            stale_bias_measurable=False, availability_bias_measurable=False,
            passed=False, blockers=("no_settled_rows",),
        )

    probs = [_clip_probability(float(r["probability"])) for r in settled]
    outs = [int(r["outcome"]) for r in settled]
    wts = [max(float(r.get("weight", 1.0)), 0.0) for r in settled]
    intercept, slope = _fit_logistic_calibration(probs, outs, wts)
    brier = _weighted_mean([(p - o) ** 2 for p, o in zip(probs, outs, strict=True)], wts)

    consensus_rows = [r for r in settled if r.get("consensus_probability") is not None]
    consensus_brier = brier_gap = None
    if len(consensus_rows) >= max(100, len(settled) // 2):
        consensus_brier = mean(
            (_clip_probability(float(r["consensus_probability"])) - int(r["outcome"])) ** 2
            for r in consensus_rows
        )
        brier_gap = brier - consensus_brier

    selected_rows = [r for r in settled if bool(r.get("selected", False))
                     and r.get("offered_decimal_odds") is not None]
    roi = mean(_bet_return(r) for r in selected_rows) if selected_rows else None
    bootstrap_lower = _cluster_bootstrap_lower(
        settled, samples=g.bootstrap_samples, seed=g.bootstrap_seed
    )
    clv_mean, clv_sample = _clv_for_selected(settled)
    stale_bias, stale_meas = _residual_bias(settled, "stale")
    avail_bias, avail_meas = _residual_bias(settled, "available")

    blockers: list[str] = []
    if len(selected_rows) < g.minimum_selected_bets:
        blockers.append("selected_bets_below_minimum")
    if bootstrap_lower is None or bootstrap_lower <= g.minimum_bootstrap_lower_roi:
        blockers.append("bootstrap_lower_roi_not_positive")
    if not (g.calibration_slope_min <= slope <= g.calibration_slope_max):
        blockers.append("calibration_slope_outside_gate")
    if stale_meas and stale_bias is not None and stale_bias > g.maximum_stale_residual_bias:
        blockers.append("material_stale_line_bias")
    if avail_meas and avail_bias is not None and avail_bias > g.maximum_availability_residual_bias:
        blockers.append("material_availability_bias")

    return MarketGateResult(
        market=market,
        sample_size=len(settled),
        selected_bets=len(selected_rows),
        game_count=len({str(r.get("game_id") or "") for r in settled}),
        calibration_slope=slope,
        calibration_intercept=intercept,
        brier=brier,
        log_loss=_log_loss(settled),
        consensus_brier=consensus_brier,
        brier_gap_to_consensus=brier_gap,
        after_vig_roi=roi,
        bootstrap_roi_lower_95=bootstrap_lower,
        clv_mean=clv_mean,
        clv_sample=clv_sample,
        stale_residual_bias=stale_bias,
        availability_residual_bias=avail_bias,
        stale_bias_measurable=stale_meas,
        availability_bias_measurable=avail_meas,
        passed=not blockers,
        blockers=tuple(blockers),
    )


def evaluate_by_market(
    rows: Iterable[Mapping[str, Any]],
    *,
    gate: PerMarketGate | None = None,
    markets: Sequence[str] | None = None,
) -> dict[str, MarketGateResult]:
    """Prepare rows once, then run the per-market gate on each eligible market."""
    prepared = prepare_oos_rows(rows, markets=markets)
    by_market: dict[str, list[dict[str, Any]]] = {}
    for row in prepared:
        by_market.setdefault(str(row.get("market_key")), []).append(row)
    wanted = markets if markets is not None else ELIGIBLE_MARKETS
    return {
        m: evaluate_market(by_market.get(m, []), market=m, gate=gate)
        for m in wanted
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            rows.append(payload)
    return rows
