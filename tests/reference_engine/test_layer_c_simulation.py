from __future__ import annotations

import math

from wnba_inplay.rotation import PlayerRotationProfile
from wnba_inplay.simulation import (
    DiscretePMF,
    InPlaySimulator,
    PlayerEventProfile,
    TeamSimulationProfile,
    combination_pmf,
    game_market_pmf,
    monte_carlo_standard_error,
    player_stat_pmf,
)
from wnba_inplay.state_engine import StateEngine


def build_inputs(config, start_event):
    state = StateEngine(config).apply(start_event)
    state.period = 4
    state.clock_seconds = 180
    state.home_score = 70
    state.away_score = 68

    rotation_profiles = {}
    event_profiles = {}
    for player in state.players.values():
        number = int(player.player_id.split("-")[-1])
        rotation_profiles[player.player_id] = PlayerRotationProfile(
            player_id=player.player_id,
            target_total_minutes=32 if number <= 5 else 12,
            closing_priority=1.0 if number <= 5 else 0.0,
        )
        event_profiles[player.player_id] = PlayerEventProfile(
            player_id=player.player_id,
            usage_weight=1.5 if number <= 2 else 1.0,
            three_point_share=0.34,
            two_point_pct=0.50,
            three_point_pct=0.35,
            free_throw_pct=0.80,
            turnover_probability=0.11,
            shooting_foul_probability=0.12,
            assist_weight=1.4 if number == 1 else 1.0,
            offensive_rebound_weight=1.5 if number >= 4 else 0.8,
            defensive_rebound_weight=1.5 if number >= 4 else 0.8,
            steal_probability=0.035,
            block_probability=0.025 if number >= 4 else 0.008,
        )

    team_profiles = {
        "HOME": TeamSimulationProfile("HOME", pace_per_40=79),
        "AWAY": TeamSimulationProfile("AWAY", pace_per_40=77),
    }
    return state, rotation_profiles, event_profiles, team_profiles


def test_simulation_is_reproducible(config, start_event):
    args = build_inputs(config, start_event)
    simulator = InPlaySimulator()
    first = simulator.simulate(*args, simulations=40, seed=99)
    second = simulator.simulate(*args, simulations=40, seed=99)
    assert first == second


def test_simulation_preserves_observed_score_and_stats(config, start_event):
    state, rotations, events, teams = build_inputs(config, start_event)
    state.players["HOME-1"].stats.points = 20
    paths = InPlaySimulator().simulate(
        state, rotations, events, teams, simulations=50, seed=2
    )
    assert all(path.home_score >= 70 for path in paths)
    assert all(path.away_score >= 68 for path in paths)
    assert all(path.player_stats["HOME-1"].points >= 20 for path in paths)


def test_final_games_are_not_tied(config, start_event):
    args = build_inputs(config, start_event)
    paths = InPlaySimulator().simulate(*args, simulations=100, seed=3)
    assert all(path.home_score != path.away_score for path in paths)


def test_player_pmf_is_normalized(config, start_event):
    args = build_inputs(config, start_event)
    paths = InPlaySimulator().simulate(*args, simulations=150, seed=4)
    pmf = player_stat_pmf(paths, "HOME-1", "points")
    assert math.isclose(sum(pmf.probabilities.values()), 1.0)
    assert pmf.variance() >= 0


def test_integer_line_push_probability_matches_pmf(config, start_event):
    args = build_inputs(config, start_event)
    paths = InPlaySimulator().simulate(*args, simulations=200, seed=5)
    pmf = player_stat_pmf(paths, "HOME-1", "points")
    line = round(pmf.mean())
    opu = pmf.over_push_under(line)
    assert opu.p_push == pmf.probabilities.get(line, 0.0)
    assert math.isclose(opu.p_over + opu.p_push + opu.p_under, 1.0)


def test_combination_prop_uses_same_path_stats(config, start_event):
    args = build_inputs(config, start_event)
    paths = InPlaySimulator().simulate(*args, simulations=80, seed=6)
    combo = combination_pmf(
        paths,
        "HOME-1",
        ("points", "rebounds", "assists"),
    )
    direct_values = [
        path.player_stats["HOME-1"].points
        + path.player_stats["HOME-1"].rebounds
        + path.player_stats["HOME-1"].assists
        for path in paths
    ]
    assert combo == DiscretePMF.from_samples(direct_values)


def test_game_markets_derive_from_shared_paths(config, start_event):
    args = build_inputs(config, start_event)
    paths = InPlaySimulator().simulate(*args, simulations=80, seed=7)
    margin = game_market_pmf(paths, lambda path: path.home_margin)
    total = game_market_pmf(paths, lambda path: path.game_total)
    assert min(margin.probabilities) >= -100
    assert min(total.probabilities) >= 138


def test_removed_player_receives_no_new_stats(config, start_event):
    state, rotations, events, teams = build_inputs(config, start_event)
    state.players["HOME-6"].active = False
    state.players["HOME-6"].injury_state = "removed"
    before = state.players["HOME-6"].stats
    paths = InPlaySimulator().simulate(
        state, rotations, events, teams, simulations=30, seed=8
    )
    assert all(path.player_stats["HOME-6"] == before for path in paths)


def test_monte_carlo_standard_error_boundaries():
    assert monte_carlo_standard_error(0.0, 100) == 0.0
    assert monte_carlo_standard_error(1.0, 100) == 0.0
    assert monte_carlo_standard_error(0.5, 100) == 0.05
