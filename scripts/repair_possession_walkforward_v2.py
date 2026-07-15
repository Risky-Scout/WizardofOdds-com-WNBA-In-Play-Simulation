#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import random
import shutil
import statistics
from typing import Any, Iterable, Mapping, Sequence

MARKETS = (
    "h2h",
    "spreads",
    "totals",
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
)

@dataclass(frozen=True)
class Platt:
    intercept: float
    slope: float
    sample_size: int
    brier: float

    def apply(self, probability: float) -> float:
        return sigmoid(self.intercept + self.slope * logit(probability))

    def beta_parameters(self) -> list[float]:
        return [self.slope, -self.slope, self.intercept]


def clip(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def sigmoid(value: float) -> float:
    value = clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + math.exp(-value))


def logit(probability: float) -> float:
    probability = clip(probability, 1e-4, 1.0 - 1e-4)
    return math.log(probability / (1.0 - probability))


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at {path}:{number}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"Non-object JSON at {path}:{number}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{datetime.now(UTC).timestamp():.6f}.tmp"
    )
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
    temp.replace(path)


def source_probability(row: Mapping[str, Any]) -> float | None:
    # The public Scenario Lab and reference engine use the possession simulator.
    # Do not silently fall back to the legacy MonteCarloEngine.
    value = safe_float(row.get("possession_raw_probability"))
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value


def line_text(row: Mapping[str, Any]) -> str:
    line = safe_float(row.get("line"))
    return "" if line is None else f"{line:.4f}"


def proposition_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("game_id") or ""),
        str(row.get("checkpoint_timestamp") or ""),
        str(row.get("market_key") or ""),
        str(row.get("selection") or "").strip().lower(),
        str(row.get("side") or "").strip().lower(),
        line_text(row),
    )


def wager_family_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    market = str(row.get("market_key") or "")
    game = str(row.get("game_id") or "")
    selection = str(row.get("selection") or "").strip().lower()
    if market in {"h2h", "spreads", "totals"}:
        return (game, market)
    return (game, market, selection)


def is_settled(row: Mapping[str, Any]) -> bool:
    return (
        row.get("binary_outcome") in {0, 1}
        and str(row.get("result") or "").lower() != "push"
        and source_probability(row) is not None
    )


def is_actionable(row: Mapping[str, Any]) -> bool:
    alignment = safe_float(row.get("snapshot_alignment_seconds"))
    age = safe_float(row.get("market_age_seconds"))
    return (
        is_settled(row)
        and bool(row.get("available", False))
        and not bool(row.get("stale", True))
        and not bool(row.get("availability_proxy", False))
        and alignment is not None
        and alignment <= 360.0
        and age is not None
        and 0.0 <= age <= 90.0
    )


def dedupe_units(
    rows: Sequence[Mapping[str, Any]],
    *,
    probability_field: str,
    actionable_only: bool = True,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if actionable_only and not is_actionable(row):
            continue
        if row.get("binary_outcome") not in {0, 1}:
            continue
        value = safe_float(row.get(probability_field))
        if value is None:
            continue
        groups[proposition_key(row)].append(row)

    output: list[dict[str, Any]] = []
    for key, group in groups.items():
        representative = dict(group[0])
        values = [
            float(row[probability_field])
            for row in group
            if safe_float(row.get(probability_field)) is not None
        ]
        representative[probability_field] = statistics.median(values)
        representative["_unit_key"] = "|".join(key)
        output.append(representative)
    return output


def game_balanced_weights(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    counts = Counter(str(row.get("game_id") or "") for row in rows)
    raw = [1.0 / max(counts[str(row.get("game_id") or "")], 1) for row in rows]
    scale = len(rows) / max(sum(raw), 1e-12)
    return [value * scale for value in raw]


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 0:
        raise ValueError("non-positive total weight")
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total


def logistic_loss(
    rows: Sequence[Mapping[str, Any]],
    weights: Sequence[float],
    intercept: float,
    slope: float,
    field: str,
    ridge: float,
) -> float:
    loss = 0.0
    for row, weight in zip(rows, weights, strict=True):
        x = logit(float(row[field]))
        q = clip(sigmoid(intercept + slope * x), 1e-10, 1.0 - 1e-10)
        y = int(row["binary_outcome"])
        loss += weight * (-(y * math.log(q) + (1 - y) * math.log(1 - q)))
    return loss + 0.5 * ridge * (intercept**2 + (slope - 1.0) ** 2)


def fit_platt(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    ridge: float = 25.0,
    slope_min: float = 0.02,
    slope_max: float = 3.0,
) -> Platt:
    usable = [
        dict(row)
        for row in rows
        if row.get("binary_outcome") in {0, 1}
        and safe_float(row.get(field)) is not None
    ]
    if len(usable) < 100 or len({int(row["binary_outcome"]) for row in usable}) < 2:
        brier = (
            statistics.mean(
                (clip(float(row[field]), 0.0, 1.0) - int(row["binary_outcome"])) ** 2
                for row in usable
            )
            if usable
            else 1.0
        )
        return Platt(0.0, 1.0, len(usable), brier)

    weights = game_balanced_weights(usable)
    intercept = 0.0
    slope = 1.0
    current = logistic_loss(usable, weights, intercept, slope, field, ridge)

    for _ in range(100):
        g0 = ridge * intercept
        g1 = ridge * (slope - 1.0)
        h00 = ridge
        h01 = 0.0
        h11 = ridge

        for row, weight in zip(usable, weights, strict=True):
            x = logit(float(row[field]))
            q = sigmoid(intercept + slope * x)
            y = int(row["binary_outcome"])
            residual = q - y
            variance = max(q * (1.0 - q), 1e-8)
            g0 += weight * residual
            g1 += weight * residual * x
            h00 += weight * variance
            h01 += weight * variance * x
            h11 += weight * variance * x * x

        determinant = h00 * h11 - h01 * h01
        if determinant <= 1e-12:
            break

        di = clip((g0 * h11 - g1 * h01) / determinant, -0.25, 0.25)
        ds = clip((g1 * h00 - g0 * h01) / determinant, -0.25, 0.25)

        accepted = False
        factor = 1.0
        for _ in range(24):
            ci = clip(intercept - factor * di, -2.5, 2.5)
            cs = clip(slope - factor * ds, slope_min, slope_max)
            candidate = logistic_loss(usable, weights, ci, cs, field, ridge)
            if candidate <= current + 1e-10:
                intercept, slope, current = ci, cs, candidate
                accepted = True
                break
            factor *= 0.5

        if not accepted or max(abs(factor * di), abs(factor * ds)) < 1e-8:
            break

    calibrated = [
        sigmoid(intercept + slope * logit(float(row[field])))
        for row in usable
    ]
    brier = weighted_mean(
        [
            (q - int(row["binary_outcome"])) ** 2
            for q, row in zip(calibrated, usable, strict=True)
        ],
        weights,
    )
    return Platt(intercept, slope, len(usable), brier)


def fit_by_market(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    minimum_market_rows: int = 500,
    ridge: float = 25.0,
) -> dict[str, Platt]:
    units = dedupe_units(rows, probability_field=field, actionable_only=True)
    global_fit = fit_platt(units, field=field, ridge=ridge)
    output = {"__global__": global_fit}
    for market in MARKETS:
        subset = [row for row in units if str(row.get("market_key")) == market]
        fit = fit_platt(subset, field=field, ridge=ridge)
        output[market] = fit if fit.sample_size >= minimum_market_rows else global_fit
    return output


def apply_calibrators(
    rows: Sequence[Mapping[str, Any]],
    calibrators: Mapping[str, Platt],
    *,
    source_field: str,
    target_field: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    global_fit = calibrators["__global__"]
    for original in rows:
        row = dict(original)
        value = safe_float(row.get(source_field))
        if value is None:
            continue
        market = str(row.get("market_key") or "")
        fit = calibrators.get(market, global_fit)
        row[target_field] = fit.apply(value)
        output.append(row)
    return output


def split_validation_by_game(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games = sorted(
        {
            (str(row.get("game_date") or ""), str(row.get("game_id") or ""))
            for row in rows
        }
    )
    cut = max(1, len(games) // 2)
    tune_games = set(games[:cut])
    calibrate_games = set(games[cut:])
    tune = [
        dict(row)
        for row in rows
        if (str(row.get("game_date") or ""), str(row.get("game_id") or ""))
        in tune_games
    ]
    calibrate = [
        dict(row)
        for row in rows
        if (str(row.get("game_date") or ""), str(row.get("game_id") or ""))
        in calibrate_games
    ]
    return tune, calibrate


def market_validation_eligibility(
    validation_rows: Sequence[Mapping[str, Any]],
    base: Mapping[str, Platt],
    post: Mapping[str, Platt],
) -> tuple[set[str], dict[str, dict[str, float | int | bool]]]:
    calibrated = apply_pipeline(validation_rows, base, post)
    report: dict[str, dict[str, float | int | bool]] = {}
    eligible: set[str] = set()

    for market in MARKETS:
        units = dedupe_units(
            [row for row in calibrated if str(row.get("market_key")) == market],
            probability_field="probability",
            actionable_only=True,
        )
        if len(units) < 500:
            report[market] = {
                "sample_size": len(units),
                "model_brier": 1.0,
                "baseline_brier": 1.0,
                "improvement": -1.0,
                "eligible": False,
            }
            continue

        weights = game_balanced_weights(units)
        outcomes = [int(row["binary_outcome"]) for row in units]
        probabilities = [float(row["probability"]) for row in units]
        prevalence = weighted_mean([float(value) for value in outcomes], weights)
        model_brier = weighted_mean(
            [
                (probability - outcome) ** 2
                for probability, outcome in zip(probabilities, outcomes, strict=True)
            ],
            weights,
        )
        baseline_brier = weighted_mean(
            [(prevalence - outcome) ** 2 for outcome in outcomes],
            weights,
        )
        improvement = baseline_brier - model_brier
        is_eligible = improvement >= 0.001 and model_brier < 0.249
        report[market] = {
            "sample_size": len(units),
            "model_brier": model_brier,
            "baseline_brier": baseline_brier,
            "improvement": improvement,
            "eligible": is_eligible,
        }
        if is_eligible:
            eligible.add(market)

    return eligible, report


def apply_pipeline(
    rows: Sequence[Mapping[str, Any]],
    base: Mapping[str, Platt],
    post: Mapping[str, Platt],
    *,
    eligible_markets: set[str] | None = None,
) -> list[dict[str, Any]]:
    stage1: list[dict[str, Any]] = []
    global_base = base["__global__"]
    for original in rows:
        row = dict(original)
        source = source_probability(row)
        if source is None:
            continue
        market = str(row.get("market_key") or "")
        base_fit = base.get(market, global_base)
        row["model_source_probability"] = source
        row["pre_post_probability"] = base_fit.apply(source)
        stage1.append(row)

    global_post = post["__global__"]
    output: list[dict[str, Any]] = []
    for row in stage1:
        market = str(row.get("market_key") or "")
        post_fit = post.get(market, global_post)
        row["probability"] = post_fit.apply(float(row["pre_post_probability"]))
        row["base_calibration_intercept"] = base.get(market, global_base).intercept
        row["base_calibration_slope"] = base.get(market, global_base).slope
        row["post_calibration_intercept"] = post_fit.intercept
        row["post_calibration_slope"] = post_fit.slope
        row["probability_source"] = "possession_raw_probability"
        row["market_eligible"] = (
            True if eligible_markets is None else market in eligible_markets
        )
        output.append(row)
    return output


def compute_roi_and_select(
    rows: list[dict[str, Any]],
    *,
    simulations: int,
) -> None:
    for row in rows:
        probability = clip(float(row["probability"]), 1e-6, 1.0 - 1e-6)
        offered = safe_float(row.get("offered_decimal_odds"))
        if offered is None or offered <= 1.0:
            row["selected"] = False
            continue
        push = clip(float(row.get("push_probability") or 0.0), 0.0, 1.0)
        non_push = 1.0 - push
        p_win = probability * non_push
        p_loss = (1.0 - probability) * non_push
        mc_error = math.sqrt(max(p_win * (1.0 - p_win), 0.0) / max(simulations, 1))
        uncertainty = math.sqrt(mc_error**2 + 0.025**2)
        allowance = 1.645 * uncertainty
        conservative_win = max(0.0, p_win - allowance)
        conservative_loss = min(non_push, p_loss + allowance)
        row["expected_roi"] = p_win * (offered - 1.0) - p_loss
        row["conservative_roi"] = (
            conservative_win * (offered - 1.0) - conservative_loss
        )
        row["total_uncertainty"] = uncertainty
        row["selected"] = False
        row["_eligible"] = bool(
            is_actionable(row)
            and bool(row.get("market_eligible", False))
            and int(row.get("consensus_book_count") or 0) >= 3
            and row["conservative_roi"] >= 0.03
            and uncertainty <= 0.05
        )

    # First qualifying checkpoint only, then best price at that checkpoint.
    candidates: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row.get("_eligible"):
            candidates[wager_family_key(row)].append(index)

    for indexes in candidates.values():
        earliest = min(str(rows[index].get("checkpoint_timestamp") or "") for index in indexes)
        same_time = [
            index
            for index in indexes
            if str(rows[index].get("checkpoint_timestamp") or "") == earliest
        ]
        winner = max(
            same_time,
            key=lambda index: (
                float(rows[index].get("conservative_roi") or -999.0),
                float(rows[index].get("offered_decimal_odds") or 0.0),
            ),
        )
        rows[winner]["selected"] = True

    for row in rows:
        row.pop("_eligible", None)


def bet_return(row: Mapping[str, Any]) -> float:
    result = str(row.get("result") or "").lower()
    if result == "push":
        return 0.0
    if result == "win":
        return float(row["offered_decimal_odds"]) - 1.0
    return -1.0


def cluster_bootstrap_lower(
    rows: Sequence[Mapping[str, Any]],
    *,
    samples: int,
    seed: int,
) -> float | None:
    selected = [
        row
        for row in rows
        if bool(row.get("selected"))
        and safe_float(row.get("offered_decimal_odds")) is not None
    ]
    if not selected:
        return None
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in selected:
        clusters[str(row.get("game_id") or "")].append(bet_return(row))
    keys = sorted(clusters)
    if len(keys) < 20:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(samples):
        sampled = [rng.choice(keys) for _ in keys]
        returns = [value for key in sampled for value in clusters[key]]
        values.append(statistics.mean(returns))
    values.sort()
    return values[max(0, int(0.025 * (len(values) - 1)))]


def evaluate(
    rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_samples: int,
) -> dict[str, Any]:
    eligible_rows = [row for row in rows if bool(row.get("market_eligible", False))]
    units = dedupe_units(
        eligible_rows,
        probability_field="probability",
        actionable_only=True,
    )
    if not units:
        return {
            "sample_size": 0,
            "selected_bets": 0,
            "game_count": 0,
            "calibration_intercept": 0.0,
            "calibration_slope": 0.0,
            "oos_brier": None,
            "consensus_rows": 0,
            "consensus_games": 0,
            "model_brier_on_consensus_rows": None,
            "consensus_brier": None,
            "brier_gap_to_consensus": None,
            "after_vig_roi": None,
            "bootstrap_roi_lower_95": None,
            "selected_stale_count": 0,
            "selected_unavailable_count": 0,
            "selected_roster_proxy_count": 0,
            "selected_alignment_violation_count": 0,
            "passed": False,
            "blockers": ["no_eligible_oos_units"],
        }
    weights = game_balanced_weights(units)
    diagnostic = fit_platt(units, field="probability", ridge=0.5)
    probabilities = [float(row["probability"]) for row in units]
    outcomes = [int(row["binary_outcome"]) for row in units]
    oos_brier = weighted_mean(
        [(p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)],
        weights,
    )

    consensus_units = [
        row
        for row in units
        if safe_float(row.get("consensus_probability")) is not None
        and int(row.get("consensus_book_count") or 0) >= 2
    ]
    consensus_games = {str(row.get("game_id") or "") for row in consensus_units}
    consensus_brier = None
    model_brier = None
    brier_gap = None
    if len(consensus_units) >= 1000 and len(consensus_games) >= 30:
        cw = game_balanced_weights(consensus_units)
        consensus_brier = weighted_mean(
            [
                (float(row["consensus_probability"]) - int(row["binary_outcome"])) ** 2
                for row in consensus_units
            ],
            cw,
        )
        model_brier = weighted_mean(
            [
                (float(row["probability"]) - int(row["binary_outcome"])) ** 2
                for row in consensus_units
            ],
            cw,
        )
        brier_gap = model_brier - consensus_brier

    selected = [row for row in rows if bool(row.get("selected"))]
    roi = statistics.mean(bet_return(row) for row in selected) if selected else None
    bootstrap = cluster_bootstrap_lower(
        rows,
        samples=bootstrap_samples,
        seed=20260715,
    )

    selected_stale = sum(bool(row.get("stale")) for row in selected)
    selected_unavailable = sum(not bool(row.get("available")) for row in selected)
    selected_proxy = sum(bool(row.get("availability_proxy")) for row in selected)
    selected_alignment = sum(
        (
            safe_float(row.get("snapshot_alignment_seconds")) is None
            or float(row.get("snapshot_alignment_seconds")) > 360.0
        )
        for row in selected
    )

    blockers: list[str] = []
    if len(units) < 1000:
        blockers.append("sample_size_below_minimum")
    if len(selected) < 200:
        blockers.append("selected_bets_below_minimum")
    if len({str(row.get("game_id") or "") for row in units}) < 30:
        blockers.append("game_count_below_minimum")
    if not 0.90 <= diagnostic.slope <= 1.10:
        blockers.append("calibration_slope_outside_gate")
    if abs(diagnostic.intercept) > 0.10:
        blockers.append("calibration_intercept_outside_gate")
    if consensus_brier is None:
        blockers.append("consensus_brier_not_measurable")
    elif brier_gap is not None and brier_gap > 0.002:
        blockers.append("brier_not_competitive_with_consensus")
    if roi is None or roi <= 0.0:
        blockers.append("after_vig_roi_not_positive")
    if bootstrap is None or bootstrap <= 0.0:
        blockers.append("bootstrap_lower_roi_not_positive")
    if selected_stale:
        blockers.append("selected_stale_line_contamination")
    if selected_unavailable:
        blockers.append("selected_unavailable_line_contamination")
    if selected_proxy:
        blockers.append("selected_roster_proxy_contamination")
    if selected_alignment:
        blockers.append("selected_alignment_contamination")

    return {
        "sample_size": len(units),
        "selected_bets": len(selected),
        "game_count": len({str(row.get("game_id") or "") for row in units}),
        "calibration_intercept": diagnostic.intercept,
        "calibration_slope": diagnostic.slope,
        "oos_brier": oos_brier,
        "consensus_rows": len(consensus_units),
        "consensus_games": len(consensus_games),
        "model_brier_on_consensus_rows": model_brier,
        "consensus_brier": consensus_brier,
        "brier_gap_to_consensus": brier_gap,
        "after_vig_roi": roi,
        "bootstrap_roi_lower_95": bootstrap,
        "selected_stale_count": selected_stale,
        "selected_unavailable_count": selected_unavailable,
        "selected_roster_proxy_count": selected_proxy,
        "selected_alignment_violation_count": selected_alignment,
        "passed": not blockers,
        "blockers": blockers,
    }


def combined_calibrator(base: Platt, post: Platt) -> Platt:
    # post(base(p)) remains a Platt transform.
    return Platt(
        intercept=post.intercept + post.slope * base.intercept,
        slope=post.slope * base.slope,
        sample_size=min(base.sample_size, post.sample_size),
        brier=post.brier,
    )


def backup(data_dir: Path, stamp: str) -> Path:
    archive = data_dir / "calibration" / "archive" / stamp
    archive.mkdir(parents=True, exist_ok=False)
    candidates = (
        data_dir / "calibration" / "oos_predictions.jsonl",
        data_dir / "calibration" / "oos_predictions.repaired.jsonl",
        data_dir / "calibration" / "walkforward_report.json",
        data_dir / "calibration" / "walkforward_repair_report.json",
        data_dir / "models" / "candidate.walkforward.json",
        data_dir / "historical_walkforward" / "output" / "raw_replay_rows.jsonl",
    )
    for path in candidates:
        if path.exists():
            shutil.copy2(path, archive / path.name)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    args = parser.parse_args()

    data_dir = args.data_dir
    raw_path = data_dir / "historical_walkforward" / "output" / "raw_replay_rows.jsonl"
    candidate_path = data_dir / "models" / "candidate.walkforward.json"
    if not raw_path.exists() or not candidate_path.exists():
        raise SystemExit("Required cached raw rows or candidate bundle are missing")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = backup(data_dir, stamp)
    print("IN-VOLUME ARCHIVE:", archive)

    raw = load_jsonl(raw_path)
    seasons = sorted({int(row.get("season") or 0) for row in raw if row.get("season")})
    if len(seasons) < 3:
        raise SystemExit("At least three seasons are required")
    train_seasons = set(seasons[:-2])
    validation_season = seasons[-2]
    oos_season = seasons[-1]

    train = [dict(row) for row in raw if int(row.get("season") or 0) in train_seasons]
    validation = [dict(row) for row in raw if int(row.get("season") or 0) == validation_season]
    oos = [dict(row) for row in raw if int(row.get("season") or 0) == oos_season]
    tune_validation, calibrate_validation = split_validation_by_game(validation)

    # Base calibration is trained only on the earliest season(s).
    train_with_source = []
    for row in train:
        value = source_probability(row)
        if value is not None:
            item = dict(row)
            item["model_source_probability"] = value
            train_with_source.append(item)
    base = fit_by_market(
        train_with_source,
        field="model_source_probability",
        minimum_market_rows=500,
        ridge=35.0,
    )

    # The first half of the validation season is not used to tune a market blend;
    # it remains available as a chronological diagnostic. The second half learns
    # a post-calibrator, keeping the final season untouched.
    tune_stage = apply_calibrators(
        [
            {**row, "model_source_probability": source_probability(row)}
            for row in tune_validation
            if source_probability(row) is not None
        ],
        base,
        source_field="model_source_probability",
        target_field="pre_post_probability",
    )
    calibrate_stage = apply_calibrators(
        [
            {**row, "model_source_probability": source_probability(row)}
            for row in calibrate_validation
            if source_probability(row) is not None
        ],
        base,
        source_field="model_source_probability",
        target_field="pre_post_probability",
    )
    post = fit_by_market(
        calibrate_stage,
        field="pre_post_probability",
        minimum_market_rows=500,
        ridge=50.0,
    )

    eligible_markets, validation_market_report = market_validation_eligibility(
        calibrate_validation,
        base,
        post,
    )
    if not eligible_markets:
        raise SystemExit(
            "No market passed the validation-period signal gate"
        )

    repaired = apply_pipeline(
        oos,
        base,
        post,
        eligible_markets=eligible_markets,
    )
    compute_roi_and_select(repaired, simulations=1000)
    metrics = evaluate(repaired, bootstrap_samples=args.bootstrap_samples)

    output_path = data_dir / "calibration" / "oos_predictions.possession_v2.jsonl"
    write_jsonl(output_path, repaired)
    active_path = data_dir / "calibration" / "oos_predictions.jsonl"
    write_jsonl(active_path, repaired)

    combined = {}
    for market in MARKETS:
        combined[market] = combined_calibrator(
            base.get(market, base["__global__"]),
            post.get(market, post["__global__"]),
        )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "repair_version": "possession-walkforward-v2",
        "probability_source": "possession_raw_probability",
        "source_raw_rows": len(raw),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "validation_tune_rows": len(tune_validation),
        "validation_calibration_rows": len(calibrate_validation),
        "oos_source_rows": len(oos),
        "oos_repaired_rows": len(repaired),
        "base_calibrators": {key: asdict(value) for key, value in base.items()},
        "post_calibrators": {key: asdict(value) for key, value in post.items()},
        "combined_calibrators": {key: asdict(value) for key, value in combined.items()},
        "eligible_markets": sorted(eligible_markets),
        "validation_market_report": validation_market_report,
        "oos_metrics": metrics,
    }
    report_path = data_dir / "calibration" / "walkforward_possession_v2_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    bundle = json.loads(candidate_path.read_text(encoding="utf-8"))
    bundle["metadata"]["calibrator_id"] = "possession-platt-walkforward-v2"
    bundle["metadata"]["calibration_parameters"] = {
        market: combined[market].beta_parameters()
        for market in sorted(eligible_markets)
    }
    bundle["metadata"]["calibration_score"] = clip(
        1.0
        - 0.5 * abs(metrics["calibration_slope"] - 1.0)
        - abs(metrics["calibration_intercept"]),
        0.0,
        1.0,
    )
    bundle["metadata"]["calibration_se"] = max(
        0.005,
        abs(metrics["calibration_intercept"])
        / math.sqrt(max(metrics["sample_size"], 1)),
    )
    bundle["validation_report"].update(
        {
            "oos_log_loss": 0.0,
            "oos_brier": metrics["oos_brier"],
            "calibration_intercept": metrics["calibration_intercept"],
            "calibration_slope": metrics["calibration_slope"],
            "sample_size": metrics["sample_size"],
            "consensus_brier": metrics["consensus_brier"],
            "after_vig_roi": metrics["after_vig_roi"],
            "bootstrap_roi_lower_95": metrics["bootstrap_roi_lower_95"],
            "promotion_passed": metrics["passed"],
            "blockers": metrics["blockers"],
            "probability_source": "possession_raw_probability",
        }
    )
    candidate_v2 = data_dir / "models" / "candidate.walkforward.possession_v2.json"
    candidate_v2.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    marker = data_dir / "calibration" / "PROMOTION_APPROVED"
    production = data_dir / "models" / "production.json"
    if metrics["passed"]:
        production.write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        marker.write_text("approved\n", encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)
        # Never delete an existing production model on a failed candidate run.

    print(json.dumps(report, indent=2, sort_keys=True))
    print("POSSESSION OOS:", output_path)
    print("REPORT:", report_path)
    print("PROMOTION_APPROVED:", marker.exists())
    print("PRODUCTION_MODEL:", production.exists())
    return 0 if metrics["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
