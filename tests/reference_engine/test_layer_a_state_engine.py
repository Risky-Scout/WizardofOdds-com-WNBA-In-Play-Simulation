from __future__ import annotations

from pathlib import Path

import pytest

from conftest import event
from wnba_inplay.replay import read_jsonl, replay, write_jsonl
from wnba_inplay.state_engine import (
    DuplicateEventError,
    EventSequenceError,
    InvalidStateError,
    StateEngine,
)


def test_state_engine_applies_scoring_stat_and_substitution(config, start_event):
    engine = StateEngine(config)
    engine.apply(start_event)
    engine.apply(
        event(
            2,
            "score",
            {"team_id": "HOME", "points": 3, "player_id": "HOME-1"},
        )
    )
    engine.apply(
        event(
            3,
            "stat",
            {"player_id": "HOME-2", "stat": "assists", "delta": 1},
        )
    )
    state = engine.apply(
        event(
            4,
            "substitution",
            {
                "team_id": "HOME",
                "player_out": "HOME-5",
                "player_in": "HOME-6",
            },
        )
    )

    assert state.home_score == 3
    assert state.players["HOME-1"].stats.points == 3
    assert state.players["HOME-1"].stats.threes == 1
    assert state.players["HOME-2"].stats.assists == 1
    assert state.home_lineup == (
        "HOME-1",
        "HOME-2",
        "HOME-3",
        "HOME-4",
        "HOME-6",
    )


def test_sequence_gap_is_rejected(config, start_event):
    engine = StateEngine(config)
    engine.apply(start_event)
    with pytest.raises(EventSequenceError, match="expected sequence 2"):
        engine.apply(event(3, "possession", {"team_id": "AWAY"}))


def test_duplicate_event_id_is_rejected(config, start_event):
    engine = StateEngine(config)
    engine.apply(start_event)
    duplicate = event(
        2,
        "possession",
        {"team_id": "AWAY"},
        event_id=start_event.event_id,
    )
    with pytest.raises(DuplicateEventError):
        engine.apply(duplicate)


def test_clock_cannot_move_back_without_official_correction(config, start_event):
    engine = StateEngine(config)
    engine.apply(start_event)
    engine.apply(event(2, "clock", {"period": 1, "clock_seconds": 500}))
    with pytest.raises(InvalidStateError, match="clock cannot move backwards"):
        engine.apply(event(3, "clock", {"period": 1, "clock_seconds": 510}))


def test_official_clock_correction_is_accepted(config, start_event):
    engine = StateEngine(config)
    engine.apply(start_event)
    engine.apply(event(2, "clock", {"period": 1, "clock_seconds": 500}))
    state = engine.apply(
        event(
            3,
            "clock",
            {
                "period": 1,
                "clock_seconds": 510,
                "official_correction": True,
            },
        )
    )
    assert state.clock_seconds == 510


def test_invalid_lineup_is_rejected(config, players):
    engine = StateEngine(config)
    bad_start = event(
        1,
        "game_started",
        {
            "players": players,
            "home_lineup": ["HOME-1"] * 5,
            "away_lineup": [f"AWAY-{i}" for i in range(1, 6)],
        },
    )
    with pytest.raises(InvalidStateError, match="5 unique"):
        engine.apply(bad_start)


def test_replay_is_deterministic(config, start_event, tmp_path: Path):
    events = [
        start_event,
        event(
            2,
            "score",
            {"team_id": "HOME", "points": 2, "player_id": "HOME-1"},
        ),
        event(3, "clock", {"period": 1, "clock_seconds": 580}),
    ]
    path = tmp_path / "events.jsonl"
    write_jsonl(events, path)

    first = replay(config, read_jsonl(path))
    second = replay(config, read_jsonl(path))

    assert first.to_dict() == second.to_dict()


def test_state_copies_do_not_mutate_authoritative_state(config, start_event):
    engine = StateEngine(config)
    returned = engine.apply(start_event)
    returned.home_score = 999
    assert engine.state.home_score == 0
