from __future__ import annotations

from pathlib import Path

import pytest

from wizard_wnba.live_reference import LiveReferenceError, simulate_live_reference
from support import (
    SIX_BOOKS,
    six_book_feed,
    snapshot_with_game,
    write_player_rows,
    write_production_model,
)


def _prepare(tmp_path: Path) -> None:
    write_production_model(tmp_path)
    write_player_rows(tmp_path)


def _run(tmp_path: Path, feed, market_id: str, **kwargs):
    return simulate_live_reference(
        data_dir=tmp_path,
        snapshot=snapshot_with_game(),
        market_feed=feed,
        market_id=market_id,
        simulations=kwargs.pop("simulations", 2000),
        seed=42,
        **kwargs,
    )


@pytest.mark.parametrize("book_key,book_title", SIX_BOOKS)
def test_six_sportsbook_backend_matrix_h2h(tmp_path, book_key, book_title):
    _prepare(tmp_path)
    feed = six_book_feed("h2h")
    market_id = f"{book_key}-h2h"

    result = _run(tmp_path, feed, market_id)

    assert result["engine_status"] == "LIVE_REFERENCE_ACTIVE"
    assert result["manifest"]["status"] == "SUCCESS"
    assert result["bookmaker_key"] == book_key
    assert result["calibration_status"] == "OOS_CALIBRATED"
    assert 0.0 <= result["win_probability"] <= 1.0
    # The displayed odds equal the selected book's price.
    expected_odds = next(
        m["american_odds"] for m in feed["markets"] if m["market_id"] == market_id
    )
    assert result["american_odds"] == expected_odds


def test_h2h_semantics_side_home_away_line_null_no_projected_mean(tmp_path):
    _prepare(tmp_path)
    feed = six_book_feed("h2h")
    result = _run(tmp_path, feed, "draftkings-h2h")

    assert result["side"] in {"home", "away"}
    assert result["side"] == "home"  # selection is the home team
    assert result["line"] is None
    # No fake binary Bernoulli mean for a two-way market.
    assert result["projected_mean"] is None


def test_oos_calibrated_carries_bundle_provenance(tmp_path):
    _prepare(tmp_path)
    feed = six_book_feed("h2h")
    result = _run(tmp_path, feed, "bovada-h2h")

    assert result["probability_source"] == "possession_raw_probability"
    assert result["model_version"] == "wnba-test-walkforward-20260715"
    assert isinstance(result["model_hash"], str) and len(result["model_hash"]) == 64
    assert result["calibrator_id"].endswith(":h2h")


def test_player_points_is_oos_calibrated(tmp_path):
    _prepare(tmp_path)
    feed = six_book_feed("player_points")
    result = _run(tmp_path, feed, "fanduel-player_points")

    assert result["calibration_status"] == "OOS_CALIBRATED"
    assert result["market_key"] == "player_points"
    assert result["side"] == "over"
    assert result["line"] == 22.5
    # Player markets DO have a meaningful projected mean.
    assert result["projected_mean"] is not None


def test_unsupported_market_rejected_cleanly(tmp_path):
    _prepare(tmp_path)
    feed = {
        "markets": [
            {
                "market_id": "dk-assists",
                "canonical_game_id": "bdl-24930",
                "market_key": "player_assists",
                "selection": "WAS P1",
                "player_name": "WAS P1",
                "bookmaker_key": "draftkings",
                "bookmaker_title": "DraftKings",
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                "side": "over",
                "line": 4.5,
                "american_odds": -115,
            }
        ]
    }
    with pytest.raises(LiveReferenceError) as excinfo:
        _run(tmp_path, feed, "dk-assists")
    assert "not an eligible production market" in str(excinfo.value)


def test_pra_market_rejected_cleanly(tmp_path):
    _prepare(tmp_path)
    feed = {
        "markets": [
            {
                "market_id": "dk-pra",
                "canonical_game_id": "bdl-24930",
                "market_key": "player_points_rebounds_assists",
                "selection": "WAS P1",
                "player_name": "WAS P1",
                "bookmaker_key": "draftkings",
                "bookmaker_title": "DraftKings",
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                "side": "over",
                "line": 30.5,
                "american_odds": -110,
            }
        ]
    }
    with pytest.raises(LiveReferenceError):
        _run(tmp_path, feed, "dk-pra")


def test_all_six_books_priced_and_odds_match(tmp_path):
    """The full six-book matrix in one pass: every book prices and its
    displayed odds equal that book's own price."""
    _prepare(tmp_path)
    feed = six_book_feed("h2h")
    seen = {}
    for book_key, _title in SIX_BOOKS:
        result = _run(tmp_path, feed, f"{book_key}-h2h")
        seen[book_key] = result["american_odds"]
        assert result["bookmaker_key"] == book_key
        assert result["calibration_status"] == "OOS_CALIBRATED"
    # Each book kept its own distinct price (odds were offset per book).
    assert len(set(seen.values())) == len(SIX_BOOKS)
