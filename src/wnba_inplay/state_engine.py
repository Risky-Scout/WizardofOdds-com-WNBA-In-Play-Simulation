from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .domain import GameConfig, GameState, PlayerState
from .events import GameEvent
from .fingerprint import fingerprint


class EventSequenceError(RuntimeError):
    """Raised when an event cannot be placed in the authoritative sequence."""


class DuplicateEventError(RuntimeError):
    """Raised when an event ID has already been applied."""


class InvalidStateError(RuntimeError):
    """Raised when an event would produce an impossible game state."""


@dataclass(frozen=True)
class AppliedEvent:
    event_id: str
    sequence: int
    state_fingerprint: str


class StateEngine:
    """Deterministic, event-sourced authoritative game-state reducer."""

    def __init__(self, config: GameConfig) -> None:
        self._state = GameState(
            config=config,
            clock_seconds=config.regulation_period_seconds,
            team_fouls={config.home_team: 0, config.away_team: 0},
            timeouts={config.home_team: 5, config.away_team: 5},
        )
        self._seen_event_ids: set[str] = set()
        self._history: list[AppliedEvent] = []

    @property
    def state(self) -> GameState:
        return deepcopy(self._state)

    @property
    def history(self) -> tuple[AppliedEvent, ...]:
        return tuple(self._history)

    def apply(self, event: GameEvent) -> GameState:
        event.validate()
        self._validate_envelope(event)

        candidate = deepcopy(self._state)
        self._reduce(candidate, event)
        self._validate_state(candidate)

        candidate.sequence = event.sequence
        candidate.last_source_timestamp_ms = event.source_timestamp_ms
        candidate.last_received_timestamp_ms = event.received_timestamp_ms
        candidate.last_event_id = event.event_id

        state_hash = fingerprint(candidate.to_dict())
        self._state = candidate
        self._seen_event_ids.add(event.event_id)
        self._history.append(
            AppliedEvent(
                event_id=event.event_id,
                sequence=event.sequence,
                state_fingerprint=state_hash,
            )
        )
        return self.state

    def _validate_envelope(self, event: GameEvent) -> None:
        if event.game_id != self._state.config.game_id:
            raise InvalidStateError("event game_id does not match engine")
        if event.event_id in self._seen_event_ids:
            raise DuplicateEventError(f"duplicate event_id: {event.event_id}")
        expected = self._state.sequence + 1
        if event.sequence != expected:
            raise EventSequenceError(
                f"expected sequence {expected}, received {event.sequence}"
            )
        if event.source_timestamp_ms < self._state.last_source_timestamp_ms:
            raise InvalidStateError("source timestamp moved backwards")

    def _reduce(self, state: GameState, event: GameEvent) -> None:
        kind = event.event_type
        p = event.payload

        if kind == "game_started":
            players = p.get("players", [])
            state.players = {
                str(item["player_id"]): PlayerState(
                    player_id=str(item["player_id"]),
                    team_id=str(item["team_id"]),
                    active=bool(item.get("active", True)),
                )
                for item in players
            }
            state.home_lineup = tuple(map(str, p["home_lineup"]))
            state.away_lineup = tuple(map(str, p["away_lineup"]))
            for player_id in state.home_lineup + state.away_lineup:
                state.players[player_id].on_court = True
            state.possession_team = p.get("possession_team")
            state.status = "in_progress"
            return

        if state.status != "in_progress" and kind != "game_final":
            raise InvalidStateError("game must be in progress")

        if kind == "clock":
            new_period = int(p.get("period", state.period))
            new_clock = int(p["clock_seconds"])
            correction = bool(p.get("official_correction", False))

            if new_period < state.period:
                raise InvalidStateError("period cannot move backwards")
            if new_period == state.period and new_clock > state.clock_seconds and not correction:
                raise InvalidStateError(
                    "clock cannot move backwards without official_correction"
                )
            if new_period > state.period:
                if new_period != state.period + 1:
                    raise InvalidStateError("periods must advance one at a time")
                state.team_fouls = {
                    state.config.home_team: 0,
                    state.config.away_team: 0,
                }
            state.period = new_period
            state.clock_seconds = new_clock
            return

        if kind == "score":
            team_id = str(p["team_id"])
            points = int(p["points"])
            if points not in (1, 2, 3):
                raise InvalidStateError("score event must be 1, 2, or 3 points")
            if team_id == state.config.home_team:
                state.home_score += points
            elif team_id == state.config.away_team:
                state.away_score += points
            else:
                raise InvalidStateError("unknown scoring team")
            player_id = p.get("player_id")
            if player_id is not None:
                state.players[str(player_id)].stats.apply("points", points)
                if points == 3:
                    state.players[str(player_id)].stats.apply("threes", 1)
            return

        if kind == "stat":
            player = self._player(state, str(p["player_id"]))
            player.stats.apply(str(p["stat"]), int(p.get("delta", 1)))
            return

        if kind == "foul":
            player = self._player(state, str(p["player_id"]))
            player.stats.apply("fouls", 1)
            state.team_fouls[player.team_id] += 1
            return

        if kind == "timeout":
            team_id = str(p["team_id"])
            if team_id not in state.timeouts:
                raise InvalidStateError("unknown timeout team")
            if state.timeouts[team_id] <= 0:
                raise InvalidStateError("no timeouts remaining")
            state.timeouts[team_id] -= 1
            return

        if kind == "possession":
            team_id = str(p["team_id"])
            if team_id not in (state.config.home_team, state.config.away_team):
                raise InvalidStateError("unknown possession team")
            state.possession_team = team_id
            return

        if kind == "substitution":
            team_id = str(p["team_id"])
            player_out = str(p["player_out"])
            player_in = str(p["player_in"])
            out_state = self._player(state, player_out)
            in_state = self._player(state, player_in)

            if out_state.team_id != team_id or in_state.team_id != team_id:
                raise InvalidStateError("substitution team mismatch")
            lineup_name = (
                "home_lineup"
                if team_id == state.config.home_team
                else "away_lineup"
            )
            lineup = list(getattr(state, lineup_name))
            if player_out not in lineup or player_in in lineup:
                raise InvalidStateError("invalid substitution membership")
            lineup[lineup.index(player_out)] = player_in
            setattr(state, lineup_name, tuple(lineup))
            out_state.on_court = False
            in_state.on_court = True
            return

        if kind == "injury":
            player = self._player(state, str(p["player_id"]))
            injury_state = str(p["injury_state"])
            if injury_state not in {
                "healthy",
                "minor_limitation",
                "material_limitation",
                "probable_removal",
                "removed",
            }:
                raise InvalidStateError("invalid injury_state")
            player.injury_state = injury_state
            if injury_state == "removed":
                player.active = False
            return

        if kind == "official_correction":
            field = str(p["field"])
            value = int(p["value"])
            if value < 0:
                raise InvalidStateError("corrected value cannot be negative")
            if field == "home_score":
                state.home_score = value
            elif field == "away_score":
                state.away_score = value
            else:
                raise InvalidStateError("unsupported correction field")
            return

        if kind == "game_final":
            state.status = "final"
            state.clock_seconds = 0
            return

        raise InvalidStateError(f"unsupported event_type: {kind}")

    @staticmethod
    def _player(state: GameState, player_id: str) -> PlayerState:
        try:
            return state.players[player_id]
        except KeyError as exc:
            raise InvalidStateError(f"unknown player: {player_id}") from exc

    def _validate_state(self, state: GameState) -> None:
        if state.home_score < 0 or state.away_score < 0:
            raise InvalidStateError("scores cannot be negative")
        if state.period <= 0:
            raise InvalidStateError("period must be positive")
        max_clock = state.config.period_length(state.period)
        if not 0 <= state.clock_seconds <= max_clock:
            raise InvalidStateError("clock is outside period bounds")

        if state.status == "in_progress":
            self._validate_lineup(state, state.config.home_team, state.home_lineup)
            self._validate_lineup(state, state.config.away_team, state.away_lineup)

    @staticmethod
    def _validate_lineup(
        state: GameState,
        team_id: str,
        lineup: tuple[str, ...],
    ) -> None:
        if len(lineup) != 5 or len(set(lineup)) != 5:
            raise InvalidStateError("each active lineup must contain 5 unique players")
        for player_id in lineup:
            player = state.players.get(player_id)
            if player is None:
                raise InvalidStateError(f"lineup contains unknown player: {player_id}")
            if player.team_id != team_id:
                raise InvalidStateError("lineup contains player from wrong team")
            if not player.active:
                raise InvalidStateError("inactive player cannot be in lineup")
