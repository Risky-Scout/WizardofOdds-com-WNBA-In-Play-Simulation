from __future__ import annotations

from datetime import UTC, datetime
import math

from wizard_wnba.domain import GameState, PlayerLiveState, PlayerRateProfile
from wizard_wnba.simulation import MonteCarloEngine


def inputs():
    now = datetime.now(UTC)
    game = GameState(
        canonical_game_id="g",
        source_game_id="1",
        home_team="Home",
        away_team="Away",
        period=4,
        clock_seconds=180,
        home_score=70,
        away_score=68,
        possession_team="Home",
        event_sequence=100,
        source_timestamp=now,
        received_timestamp=now,
    )
    players = {
        "p1": PlayerLiveState(
            "p1", "Player One", "Home", True, True, 30, 100, 2,
            "healthy", 20, 8, 4, 1
        ),
        "p2": PlayerLiveState(
            "p2", "Player Two", "Away", True, True, 29, 100, 2,
            "healthy", 18, 5, 6, 2
        ),
    }
    profiles = {
        "p1": PlayerRateProfile("p1", 4, .8, .7, .3, .12, .05),
        "p2": PlayerRateProfile("p2", 4, .8, .65, .2, .2, .08),
    }
    return game, players, profiles


def test_simulation_is_reproducible():
    game, players, profiles = inputs()
    engine = MonteCarloEngine()
    first = engine.simulate(
        game=game,
        live_players=players,
        profiles=profiles,
        simulations=100,
        seed=7,
    )
    second = engine.simulate(
        game=game,
        live_players=players,
        profiles=profiles,
        simulations=100,
        seed=7,
    )
    assert first == second


def test_player_market_probabilities_normalize_and_integer_push_matches_pmf():
    game, players, profiles = inputs()
    engine = MonteCarloEngine()
    bundle = engine.simulate(
        game=game,
        live_players=players,
        profiles=profiles,
        simulations=500,
        seed=8,
    )
    result = engine.price_player_market(
        bundle=bundle,
        player_id="p1",
        market_key="player_points",
        line=22,
        side="over",
        calibration_score=.9,
        calibrator_id="raw",
        model_version="v1",
        calibration_se=.01,
        model_se=.02,
    )
    assert math.isclose(result.p_win + result.p_push + result.p_loss, 1)
    assert result.p_push == result.pmf.get(22, 0)
