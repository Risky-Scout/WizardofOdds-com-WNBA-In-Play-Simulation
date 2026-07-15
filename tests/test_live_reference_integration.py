
from __future__ import annotations

import json
from pathlib import Path

from wizard_wnba.live_reference import simulate_live_reference


def test_live_reference_simulator_runs(tmp_path: Path) -> None:
    raw = (
        tmp_path
        / "raw"
        / "provider=balldontlie"
        / "endpoint=wnba_v1_player_stats"
        / "date=2026-07-15"
    )
    raw.mkdir(parents=True)

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
                    "game": {"id": 24930},
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
        json.dumps({"payload": {"data": rows}}),
        encoding="utf-8",
    )

    snapshot = {
        "games": [
            {
                "canonical_game_id": "bdl-24930",
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
    feed = {
        "markets": [
            {
                "market_id": "market-1",
                "canonical_game_id": "bdl-24930",
                "market_key": "player_points",
                "selection": "WAS P1",
                "player_name": "WAS P1",
                "bookmaker_key": "draftkings",
                "bookmaker_title": "DraftKings",
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                "side": "over",
                "line": 22.5,
                "american_odds": -110,
            }
        ]
    }

    result = simulate_live_reference(
        data_dir=tmp_path,
        snapshot=snapshot,
        market_feed=feed,
        market_id="market-1",
        simulations=1000,
        seed=42,
    )

    assert result["engine_status"] == "LIVE_REFERENCE_ACTIVE"
    assert result["manifest"]["status"] == "SUCCESS"
    assert result["simulation_count"] == 1000
    assert 0 <= result["win_probability"] <= 1
