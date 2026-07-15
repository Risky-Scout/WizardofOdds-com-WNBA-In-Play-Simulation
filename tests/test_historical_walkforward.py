from __future__ import annotations

import math
import random

from wizard_wnba.historical_walkforward import (
    apply_beta,
    calibrators_by_market,
    chronological_split,
    fit_beta,
    leave_one_out_consensus,
)


def test_beta_fit_is_close_to_identity_for_calibrated_probabilities() -> None:
    rng = random.Random(42)
    rows = []
    for index in range(1500):
        probability = 0.25 + 0.50 * ((index % 20) / 19)
        outcome = 1 if rng.random() < probability else 0
        rows.append(
            {
                "raw_probability": probability,
                "binary_outcome": outcome,
                "result": "win" if outcome else "loss",
                "weight": 1.0,
                "market_key": "totals",
            }
        )

    parameters = fit_beta(rows)
    transformed = apply_beta(
        0.60,
        (parameters.a, parameters.b, parameters.c),
    )

    assert parameters.sample_size == 1500
    assert 0.50 < transformed < 0.70


def test_spread_consensus_pairs_opposite_signed_lines() -> None:
    offers = [
        {
            "bookmaker_key": "a",
            "market_key": "spreads",
            "name": "Home Team",
            "description": "",
            "line": -5.5,
            "american_odds": -110,
            "home_team": "Home Team",
        },
        {
            "bookmaker_key": "a",
            "market_key": "spreads",
            "name": "Away Team",
            "description": "",
            "line": 5.5,
            "american_odds": -110,
            "home_team": "Home Team",
        },
        {
            "bookmaker_key": "b",
            "market_key": "spreads",
            "name": "Home Team",
            "description": "",
            "line": -5.5,
            "american_odds": -105,
            "home_team": "Home Team",
        },
        {
            "bookmaker_key": "b",
            "market_key": "spreads",
            "name": "Away Team",
            "description": "",
            "line": 5.5,
            "american_odds": -115,
            "home_team": "Home Team",
        },
        {
            "bookmaker_key": "target",
            "market_key": "spreads",
            "name": "Home Team",
            "description": "",
            "line": -5.5,
            "american_odds": 100,
            "home_team": "Home Team",
        },
    ]

    probability, count, dispersion = leave_one_out_consensus(
        offers,
        offers[-1],
    )

    assert probability is not None
    assert count == 2
    assert 0.45 < probability < 0.55
    assert dispersion is not None


def test_chronological_split_uses_latest_season_as_oos() -> None:
    rows = [
        {
            "game_date": f"{season}-06-01T00:00:00Z",
            "game_id": f"g-{season}",
            "season": season,
        }
        for season in (2024, 2025, 2026)
        for _ in range(10)
    ]

    train, calibration, oos = chronological_split(rows)

    assert {row["season"] for row in train} == {2024}
    assert {row["season"] for row in calibration} == {2025}
    assert {row["season"] for row in oos} == {2026}


def test_market_calibrators_include_global_fallback() -> None:
    rng = random.Random(7)
    rows = []
    for index in range(200):
        p = 0.55 if index % 2 else 0.45
        outcome = 1 if rng.random() < p else 0
        rows.append(
            {
                "market_key": "totals",
                "raw_probability": p,
                "binary_outcome": outcome,
                "result": "win" if outcome else "loss",
            }
        )

    calibrators = calibrators_by_market(rows)

    assert "__global__" in calibrators
    assert "totals" in calibrators
    assert math.isfinite(calibrators["totals"].a)
