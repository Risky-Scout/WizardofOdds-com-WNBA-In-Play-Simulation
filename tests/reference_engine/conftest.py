from __future__ import annotations

import pytest

from wnba_inplay.domain import GameConfig
from wnba_inplay.events import GameEvent


@pytest.fixture
def config() -> GameConfig:
    return GameConfig(
        game_id="game-1",
        home_team="HOME",
        away_team="AWAY",
    )


@pytest.fixture
def players() -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for team in ("HOME", "AWAY"):
        for index in range(1, 8):
            values.append(
                {
                    "player_id": f"{team}-{index}",
                    "team_id": team,
                    "active": True,
                }
            )
    return values


def event(
    sequence: int,
    event_type: str,
    payload: dict | None = None,
    event_id: str | None = None,
) -> GameEvent:
    timestamp = sequence * 1000
    return GameEvent(
        event_id=event_id or f"event-{sequence}",
        game_id="game-1",
        sequence=sequence,
        event_type=event_type,
        source_timestamp_ms=timestamp,
        received_timestamp_ms=timestamp + 5,
        payload=payload or {},
    )


@pytest.fixture
def start_event(players):
    return event(
        1,
        "game_started",
        {
            "players": players,
            "home_lineup": [f"HOME-{i}" for i in range(1, 6)],
            "away_lineup": [f"AWAY-{i}" for i in range(1, 6)],
            "possession_team": "HOME",
        },
    )
