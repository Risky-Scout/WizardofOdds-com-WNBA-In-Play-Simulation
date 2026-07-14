from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from .adapters.the_odds_api import OddsQuota, TheOddsApiClient
from .settings import get_settings
from .storage import RawSnapshotStore


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def backfill_event(
    *,
    event_id: str,
    start: datetime,
    end: datetime,
    interval_minutes: int,
    markets: tuple[str, ...],
) -> None:
    settings = get_settings()
    if not settings.the_odds_api_key:
        raise RuntimeError("THE_ODDS_API_KEY is not configured")
    client = TheOddsApiClient(
        api_key=settings.the_odds_api_key,
        base_url=settings.the_odds_api_base_url,
        sport_key=settings.the_odds_api_sport_key,
    )
    store = RawSnapshotStore(settings.data_dir / "raw")
    current = start
    try:
        while current <= end:
            response = await client.historical_event_odds(
                event_id=event_id,
                snapshot_at=current,
                regions=settings.odds_regions,
                markets=markets,
                bookmakers=settings.parsed_bookmakers,
            )
            quota = OddsQuota.from_response(response)
            store.save(
                provider=response.provider,
                endpoint=response.endpoint,
                payload=response.payload,
                request_parameters={
                    "event_id": event_id,
                    "snapshot_at": current.isoformat(),
                    "markets": markets,
                },
                response_headers=response.headers,
                status_code=response.status_code,
                captured_at=datetime.now(UTC),
            )
            print(
                f"{current.isoformat()} saved "
                f"(quota remaining={quota.remaining}, last cost={quota.last_cost})"
            )
            current += timedelta(minutes=interval_minutes)
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill The Odds API historical WNBA event snapshots"
    )
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument(
        "--markets",
        default="player_points,player_rebounds,player_assists,player_threes",
    )
    return parser


def run() -> None:
    args = build_parser().parse_args()
    asyncio.run(
        backfill_event(
            event_id=args.event_id,
            start=parse_datetime(args.start),
            end=parse_datetime(args.end),
            interval_minutes=args.interval_minutes,
            markets=tuple(
                value.strip()
                for value in args.markets.split(",")
                if value.strip()
            ),
        )
    )


if __name__ == "__main__":
    run()
