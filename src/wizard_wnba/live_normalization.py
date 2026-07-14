from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .domain import GameState, PlayerLiveState
from .identity import IdentityRegistry, PlayerCrosswalk


def parse_clock_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if ":" in text:
        minutes, seconds = text.split(":", 1)
        return int(minutes) * 60 + float(seconds)
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_bdl_game(
    row: Mapping[str, Any],
    *,
    received_at: datetime,
    latest_play: Mapping[str, Any] | None = None,
) -> GameState:
    home = row["home_team"]
    away = row.get("visitor_team", row.get("away_team"))
    if away is None:
        raise ValueError("game row has no visitor/away team")

    play = latest_play or {}
    source_timestamp = received_at
    period = int(play.get("period", row.get("period", 0)) or 0)
    clock = parse_clock_seconds(play.get("clock", row.get("time", 0)))
    home_score = int(play.get("home_score", row.get("home_score", 0)) or 0)
    away_score = int(play.get("away_score", row.get("away_score", 0)) or 0)
    sequence = int(play.get("order", 0) or 0)
    status = str(row.get("status", "unknown"))

    return GameState(
        canonical_game_id=f"bdl-{row['id']}",
        source_game_id=str(row["id"]),
        home_team=str(home["full_name"]),
        away_team=str(away["full_name"]),
        period=max(period, 1),
        clock_seconds=clock,
        home_score=home_score,
        away_score=away_score,
        possession_team=None,
        event_sequence=sequence,
        source_timestamp=source_timestamp,
        received_timestamp=received_at,
        status=status,
        reconciliation_ok=True,
    )


def normalize_bdl_player_stats(
    payload: Mapping[str, Any],
    *,
    identity: IdentityRegistry,
) -> dict[str, PlayerLiveState]:
    output: dict[str, PlayerLiveState] = {}
    for row in payload.get("data", []):
        player = row["player"]
        team = row["team"]
        display_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        bdl_id = str(player["id"])
        canonical_id = f"bdl-player-{bdl_id}"
        existing = identity.resolve_name(display_name, str(team["full_name"]))
        if existing:
            canonical_id = existing.canonical_player_id
        else:
            identity.upsert(
                PlayerCrosswalk(
                    canonical_player_id=canonical_id,
                    display_name=display_name,
                    team=str(team["full_name"]),
                    balldontlie_player_id=bdl_id,
                    odds_api_name=display_name,
                    confidence=1.0,
                )
            )

        minutes_value = str(row.get("min") or "0")
        if ":" in minutes_value:
            minute, second = minutes_value.split(":", 1)
            minutes = int(minute) + int(second) / 60
        else:
            try:
                minutes = float(minutes_value)
            except ValueError:
                minutes = 0.0

        output[canonical_id] = PlayerLiveState(
            canonical_player_id=canonical_id,
            display_name=display_name,
            team=str(team["full_name"]),
            on_court=False,
            active=True,
            minutes_played=minutes,
            current_stint_seconds=0.0,
            fouls=int(row.get("pf") or 0),
            injury_state="healthy",
            points=int(row.get("pts") or 0),
            rebounds=int(row.get("reb") or 0),
            assists=int(row.get("ast") or 0),
            threes=int(row.get("fg3m") or 0),
            steals=int(row.get("stl") or 0),
            blocks=int(row.get("blk") or 0),
            turnovers=int(row.get("turnover") or 0),
        )
    identity.save()
    return output
