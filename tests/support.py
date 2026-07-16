"""Shared builders for wizard_wnba tests.

Self-contained fixtures (a synthetic promoted production model, live box-score
rows, a snapshot, and a six-sportsbook live-market feed) so tests never depend
on generated build artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The six sportsbooks the backend already proved return HTTP 200.
SIX_BOOKS: tuple[tuple[str, str], ...] = (
    ("betmgm", "BetMGM"),
    ("betrivers", "BetRivers"),
    ("bovada", "Bovada"),
    ("draftkings", "DraftKings"),
    ("fanatics", "Fanatics"),
    ("fanduel", "FanDuel"),
)

# Exactly the permitted production markets (calibrators present).
ELIGIBLE_MARKETS = (
    "h2h",
    "player_points",
    "player_rebounds",
    "player_threes",
    "spreads",
    "totals",
)


def production_model_dict() -> dict[str, Any]:
    # Near-identity Platt calibrators (a=1, b=-1, c=0) for every eligible
    # market, so calibration is well-defined and testable. Deliberately omits
    # player_assists and player_points_rebounds_assists.
    calibration_parameters = {
        market: [1.0, -1.0, 0.0] for market in ELIGIBLE_MARKETS
    }
    profiles = []
    player_id = 100
    for team, prefix in (
        ("Washington Mystics", "WAS"),
        ("Toronto Tempo", "TOR"),
    ):
        for index in range(7):
            player_id += 1
            profiles.append(
                {
                    "canonical_player_id": f"bdl-player-{player_id}",
                    "expected_remaining_minutes": 12.0,
                    "remaining_minutes_sd": 3.0,
                    "points_per_minute": 0.55,
                    "rebounds_per_minute": 0.22,
                    "assists_per_minute": 0.14,
                    "threes_per_minute": 0.05,
                    "usage_multiplier": 1.0,
                    "pace_multiplier": 1.0,
                    "role_uncertainty": 0.03,
                    "target_total_minutes": 30.0,
                }
            )
    return {
        "metadata": {
            "model_version": "wnba-test-walkforward-20260715",
            "calibrator_id": "possession-platt-test-v2",
            "calibration_score": 0.95,
            "calibration_se": 0.005,
            "model_se": 0.025,
            "calibration_parameters": calibration_parameters,
        },
        "profiles": profiles,
        "trained_through": "2026-07-14",
        "validation_report": {
            "oos_log_loss": 0.0,
            "oos_brier": 0.23,
            "calibration_slope": 0.95,
            "calibration_intercept": 0.003,
            "sample_size": 47352,
            "probability_source": "possession_raw_probability",
            "promotion_passed": True,
            # Honest per-market gate: h2h validated, others accumulating.
            "per_market_gate": {
                "h2h": {"passed": True, "selected_bets": 166,
                        "calibration_slope": 0.864,
                        "bootstrap_roi_lower_95": 0.239},
                "spreads": {"passed": False, "selected_bets": 117,
                            "calibration_slope": 1.366},
                "totals": {"passed": False, "selected_bets": 105,
                           "bootstrap_roi_lower_95": -0.13},
                "player_points": {"passed": False, "selected_bets": 0},
                "player_rebounds": {"passed": False, "selected_bets": 0},
                "player_threes": {"passed": False, "selected_bets": 0},
            },
        },
    }


def write_production_model(data_dir: Path) -> Path:
    models = data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    path = models / "production.json"
    path.write_text(json.dumps(production_model_dict()), encoding="utf-8")
    # A promotion marker alongside the model.
    (models / "PROMOTION_APPROVED").write_text("approved", encoding="utf-8")
    return path


def write_player_rows(data_dir: Path, game_id: int = 24930) -> None:
    raw = (
        data_dir
        / "raw"
        / "provider=balldontlie"
        / "endpoint=wnba_v1_player_stats"
        / "date=2026-07-15"
    )
    raw.mkdir(parents=True, exist_ok=True)
    rows = []
    player_id = 100
    for team_name, prefix in (
        ("Washington Mystics", "WAS"),
        ("Toronto Tempo", "TOR"),
    ):
        for index in range(7):
            player_id += 1
            rows.append(
                {
                    "game": {"id": game_id},
                    "player": {
                        "id": player_id,
                        "first_name": prefix,
                        "last_name": f"P{index + 1}",
                    },
                    "team": {"full_name": team_name},
                    "min": "12:00",
                    "pts": max(0, 12 - index),
                    "reb": max(1, 6 - index // 2),
                    "ast": max(0, 4 - index // 2),
                    "fg3m": 1,
                    "stl": 0,
                    "blk": 0,
                    "turnover": 1,
                    "pf": 1,
                    "fga": max(2, 10 - index),
                    "fgm": max(1, 5 - index // 2),
                    "fg3a": 3,
                    "fta": 2,
                    "ftm": 2,
                    "oreb": 1,
                    "dreb": 3,
                }
            )
    (raw / "snapshot.json").write_text(
        json.dumps({"payload": {"data": rows}}), encoding="utf-8"
    )


def snapshot_with_game(game_id: int = 24930) -> dict[str, Any]:
    return {
        "games": [
            {
                "canonical_game_id": f"bdl-{game_id}",
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                "home_score": 30,
                "away_score": 19,
                "period": 2,
                "clock_seconds": 182,
                "event_sequence": 168,
                "status": "in",
                "state_age_seconds": 2.0,
            }
        ]
    }


def six_book_feed(
    market_key: str = "h2h",
    *,
    game_id: int = 24930,
) -> dict[str, Any]:
    """A live-market feed offering the same market across all six books."""
    markets = []
    for offset, (book_key, book_title) in enumerate(SIX_BOOKS):
        if market_key == "h2h":
            common = {
                "market_key": "h2h",
                "selection": "Toronto Tempo",
                "side": "home",
                "line": None,
                "american_odds": -140 - offset,
            }
        elif market_key == "player_points":
            common = {
                "market_key": "player_points",
                "selection": "WAS P1",
                "player_name": "WAS P1",
                "side": "over",
                "line": 22.5,
                "american_odds": -110 - offset,
            }
        else:
            common = {
                "market_key": market_key,
                "selection": "Toronto Tempo",
                "side": "over",
                "line": -3.5,
                "american_odds": -110 - offset,
            }
        markets.append(
            {
                "market_id": f"{book_key}-{market_key}",
                "recommendation_id": f"{book_key}-{market_key}",
                "canonical_game_id": f"bdl-{game_id}",
                "bookmaker_key": book_key,
                "bookmaker_title": book_title,
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                **common,
            }
        )
    return {"odds_format": "american", "count": len(markets), "markets": markets}
