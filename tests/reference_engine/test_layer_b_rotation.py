from __future__ import annotations

from copy import deepcopy

import pytest

from wnba_inplay.rotation import (
    PlayerRotationProfile,
    RotationAllocator,
    RotationContext,
    game_seconds_remaining,
)
from wnba_inplay.state_engine import StateEngine


def build_state(config, start_event):
    engine = StateEngine(config)
    state = engine.apply(start_event)
    state.clock_seconds = 300
    for index, player in enumerate(state.players.values()):
        player.stats.minutes_seconds = 900 + (index % 5) * 60
    return state


def build_profiles(state):
    profiles = {}
    for player in state.players.values():
        number = int(player.player_id.split("-")[-1])
        profiles[player.player_id] = PlayerRotationProfile(
            player_id=player.player_id,
            target_total_minutes=32 if number <= 5 else 16,
            rotation_weight=1.0,
            closing_priority=1.0 if number <= 5 else 0.0,
        )
    return profiles


def test_game_seconds_remaining_uses_period_and_clock(config, start_event):
    state = build_state(config, start_event)
    assert state.period == 1
    assert game_seconds_remaining(state) == 300 + 3 * 600


def test_allocations_satisfy_exact_team_capacity(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    context = RotationContext(
        game_seconds_remaining=600,
        score_margin=4,
        overtime_probability=0.0,
    )
    samples = RotationAllocator().sample(
        state,
        profiles,
        context,
        simulations=50,
        seed=7,
    )

    for sample in samples:
        assert sum(
            seconds
            for player_id, seconds in sample.items()
            if player_id.startswith("HOME")
        ) == 5 * 600
        assert sum(
            seconds
            for player_id, seconds in sample.items()
            if player_id.startswith("AWAY")
        ) == 5 * 600
        assert all(0 <= seconds <= 600 for seconds in sample.values())


def test_removed_player_receives_zero_seconds(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    state.players["HOME-1"].injury_state = "removed"
    state.players["HOME-1"].active = False
    samples = RotationAllocator().sample(
        state,
        profiles,
        RotationContext(600, 2),
        simulations=20,
        seed=8,
    )
    assert all(sample["HOME-1"] == 0 for sample in samples)


def test_injury_reduces_expected_minutes(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    allocator = RotationAllocator()
    context = RotationContext(600, 2, uncertainty_scale=0.0)

    healthy = allocator.sample(state, profiles, context, 10, seed=1)
    healthy_mean = allocator.summarize(healthy)["HOME-1"].mean_seconds

    injured_state = deepcopy(state)
    injured_state.players["HOME-1"].injury_state = "material_limitation"
    injured = allocator.sample(injured_state, profiles, context, 10, seed=1)
    injured_mean = allocator.summarize(injured)["HOME-1"].mean_seconds

    assert injured_mean < healthy_mean


def test_rotation_sampling_is_reproducible(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    allocator = RotationAllocator()
    context = RotationContext(900, 1, overtime_probability=0.15)

    first = allocator.sample(state, profiles, context, 25, seed=123)
    second = allocator.sample(state, profiles, context, 25, seed=123)
    assert first == second


def test_summary_quantiles_are_ordered(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    samples = RotationAllocator().sample(
        state,
        profiles,
        RotationContext(900, 1, uncertainty_scale=0.25),
        100,
        seed=4,
    )
    summary = RotationAllocator().summarize(samples)["HOME-1"]
    assert summary.p10_seconds <= summary.p50_seconds <= summary.p90_seconds


def test_substitution_hazard_rises_with_stint_and_injury(config, start_event):
    state = build_state(config, start_event)
    profile = build_profiles(state)["HOME-1"]
    player = state.players["HOME-1"]
    allocator = RotationAllocator()

    player.current_stint_seconds = 120
    healthy = allocator.substitution_hazard(player, profile, 2, 500)

    player.current_stint_seconds = 600
    player.injury_state = "material_limitation"
    stressed = allocator.substitution_hazard(player, profile, 2, 500)

    assert stressed > healthy


def test_fewer_than_five_eligible_players_fails(config, start_event):
    state = build_state(config, start_event)
    profiles = build_profiles(state)
    for player_id in ("HOME-1", "HOME-2", "HOME-3"):
        state.players[player_id].active = False
        state.players[player_id].injury_state = "removed"

    with pytest.raises(ValueError, match="at least five eligible"):
        RotationAllocator().sample(
            state,
            profiles,
            RotationContext(600, 0),
            simulations=1,
            seed=1,
        )
