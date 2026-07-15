from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
import re
from typing import Any, Mapping

from .adapters.balldontlie import BallDontLieClient
from .adapters.the_odds_api import OddsQuota, TheOddsApiClient
from .settings import Settings
from .storage import RawSnapshotStore


logger = logging.getLogger(__name__)


_LIVE_STATUS_PATTERN = re.compile(
    r"\b(?:q[1-4]|[1-4](?:st|nd|rd|th)\s+(?:qtr|quarter)|"
    r"halftime|half-time|overtime|ot|in progress|live)\b",
    re.IGNORECASE,
)


def is_bdl_game_live(row: Mapping[str, Any]) -> bool:
    status = str(row.get("status", "")).strip().lower()
    period = int(row.get("period", 0) or 0)

    if (
        status in {"final", "post"}
        or status.startswith("final")
        or status.startswith("postponed")
        or status.startswith("cancelled")
        or status.startswith("canceled")
    ):
        return False

    if period > 0:
        return True

    return bool(_LIVE_STATUS_PATTERN.search(status))


def discovery_dates(captured: datetime) -> tuple[date, ...]:
    # Include the prior UTC date so late-evening U.S. games are not
    # dropped when the container passes midnight UTC.
    return (
        captured.date(),
        (captured - timedelta(days=1)).date(),
    )


@dataclass(frozen=True)
class LiveGamePayload:
    game_id: int
    plays: Mapping[str, Any]
    player_stats: Mapping[str, Any]
    player_props: Mapping[str, Any]


@dataclass(frozen=True)
class CollectionCycle:
    captured_at: datetime
    bdl_games_payload: Mapping[str, Any]
    odds_events_payload: tuple[Mapping[str, Any], ...]
    featured_odds_payload: tuple[Mapping[str, Any], ...]
    event_odds_payloads: Mapping[str, Mapping[str, Any]]
    live_game_payloads: Mapping[int, LiveGamePayload]
    quota_remaining: int | None
    errors: tuple[str, ...]

    @property
    def bdl_games(self) -> int:
        return len(self.bdl_games_payload.get("data", []))

    @property
    def odds_events(self) -> int:
        return len(self.odds_events_payload)

    @property
    def in_progress_games(self) -> int:
        return len(self.live_game_payloads)


class LiveCollector:
    """Archives provider snapshots and returns a normalized collection envelope."""

    def __init__(
        self,
        settings: Settings,
        *,
        bdl: BallDontLieClient,
        odds: TheOddsApiClient,
        snapshot_store: RawSnapshotStore,
    ) -> None:
        self.settings = settings
        self.bdl = bdl
        self.odds = odds
        self.snapshot_store = snapshot_store

    async def collect_once(self) -> CollectionCycle:
        captured = datetime.now(UTC)
        errors: list[str] = []
        bdl_games_payload: Mapping[str, Any] = {"data": []}
        odds_events_payload: list[Mapping[str, Any]] = []
        featured_odds_payload: list[Mapping[str, Any]] = []
        event_odds_payloads: dict[str, Mapping[str, Any]] = {}
        live_game_payloads: dict[int, LiveGamePayload] = {}
        quota_remaining = None

        try:
            response = await self.bdl.games(dates=discovery_dates(captured))
            bdl_games_payload = response.payload
            self._archive(response, captured)
        except Exception as exc:
            errors.append(f"balldontlie.games: {exc}")

        try:
            response = await self.odds.events()
            odds_events_payload = list(response.payload)
            self._archive(response, captured)
            quota_remaining = OddsQuota.from_response(response).remaining
        except Exception as exc:
            errors.append(f"the_odds_api.events: {exc}")

        live_rows = [
            row
            for row in bdl_games_payload.get("data", [])
            if is_bdl_game_live(row)
        ]
        for row in live_rows:
            game_id = int(row["id"])
            payload = await self._collect_live_game(
                game_id,
                captured,
                errors,
            )
            if payload is not None:
                live_game_payloads[game_id] = payload

        try:
            response = await self.odds.featured_odds(
                regions=self.settings.odds_regions,
                bookmakers=self.settings.parsed_bookmakers,
            )
            featured_odds_payload = list(response.payload)
            self._archive(response, captured)
            quota_remaining = OddsQuota.from_response(response).remaining
        except Exception as exc:
            errors.append(f"the_odds_api.featured_odds: {exc}")

        for event in odds_events_payload:
            event_id = str(event.get("id", ""))
            if not event_id:
                continue
            try:
                response = await self.odds.event_odds(
                    event_id=event_id,
                    regions=self.settings.odds_regions,
                    markets=self.settings.parsed_markets,
                    bookmakers=self.settings.parsed_bookmakers,
                )
                event_odds_payloads[event_id] = response.payload
                self._archive(response, captured)
                quota_remaining = OddsQuota.from_response(response).remaining
            except Exception as exc:
                errors.append(
                    f"the_odds_api.event_odds[{event_id}]: {exc}"
                )

        return CollectionCycle(
            captured_at=captured,
            bdl_games_payload=bdl_games_payload,
            odds_events_payload=tuple(odds_events_payload),
            featured_odds_payload=tuple(featured_odds_payload),
            event_odds_payloads=event_odds_payloads,
            live_game_payloads=live_game_payloads,
            quota_remaining=quota_remaining,
            errors=tuple(errors),
        )

    async def _collect_live_game(
        self,
        game_id: int,
        captured: datetime,
        errors: list[str],
    ) -> LiveGamePayload | None:
        calls = (
            ("plays", self.bdl.plays(game_id)),
            ("player_stats", self.bdl.player_stats(game_ids=(game_id,))),
            ("player_props", self.bdl.player_props(game_id=game_id)),
        )
        results = await asyncio.gather(
            *(coroutine for _, coroutine in calls),
            return_exceptions=True,
        )
        payloads: dict[str, Mapping[str, Any]] = {}

        for (name, _), result in zip(calls, results, strict=True):
            if isinstance(result, Exception):
                errors.append(f"balldontlie.{name}[{game_id}]: {result}")
                continue
            self._archive(result, captured)
            payloads[name] = result.payload

        if "plays" not in payloads or "player_stats" not in payloads:
            return None
        return LiveGamePayload(
            game_id=game_id,
            plays=payloads["plays"],
            player_stats=payloads["player_stats"],
            player_props=payloads.get("player_props", {"data": []}),
        )

    def _archive(self, response: Any, captured: datetime) -> None:
        self.snapshot_store.save(
            provider=response.provider,
            endpoint=response.endpoint,
            payload=response.payload,
            response_headers=response.headers,
            status_code=response.status_code,
            captured_at=captured,
        )

    async def close(self) -> None:
        await asyncio.gather(self.bdl.close(), self.odds.close())
