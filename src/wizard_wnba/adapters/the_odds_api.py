from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .http import AsyncProviderClient, ProviderResponse


@dataclass(frozen=True)
class OddsQuota:
    remaining: int | None
    used: int | None
    last_cost: int | None

    @classmethod
    def from_response(cls, response: ProviderResponse) -> "OddsQuota":
        def parse(name: str) -> int | None:
            value = response.headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        return cls(
            remaining=parse("x-requests-remaining"),
            used=parse("x-requests-used"),
            last_cost=parse("x-requests-last"),
        )


class TheOddsApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.the-odds-api.com",
        sport_key: str = "basketball_wnba",
        client: AsyncProviderClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("The Odds API key is required")
        self.api_key = api_key
        self.sport_key = sport_key
        self.http = client or AsyncProviderClient(
            provider="the_odds_api",
            base_url=base_url,
        )

    async def close(self) -> None:
        await self.http.close()

    async def events(self) -> ProviderResponse:
        return await self.http.request_json(
            "GET",
            f"/v4/sports/{self.sport_key}/events",
            params={"apiKey": self.api_key},
        )

    async def featured_odds(
        self,
        *,
        regions: str = "us",
        markets: Iterable[str] = ("h2h", "spreads", "totals"),
        bookmakers: Iterable[str] = (),
    ) -> ProviderResponse:
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        bookmaker_value = ",".join(bookmakers)
        if bookmaker_value:
            params["bookmakers"] = bookmaker_value
        return await self.http.request_json(
            "GET",
            f"/v4/sports/{self.sport_key}/odds",
            params=params,
        )

    async def event_odds(
        self,
        *,
        event_id: str,
        regions: str = "us",
        markets: Iterable[str],
        bookmakers: Iterable[str] = (),
    ) -> ProviderResponse:
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        bookmaker_value = ",".join(bookmakers)
        if bookmaker_value:
            params["bookmakers"] = bookmaker_value
        return await self.http.request_json(
            "GET",
            f"/v4/sports/{self.sport_key}/events/{event_id}/odds",
            params=params,
        )

    async def historical_featured_odds(
        self,
        *,
        snapshot_at: datetime,
        regions: str = "us",
        markets: Iterable[str] = ("h2h", "spreads", "totals"),
        bookmakers: Iterable[str] = (),
    ) -> ProviderResponse:
        params = {
            "apiKey": self.api_key,
            "date": snapshot_at.isoformat().replace("+00:00", "Z"),
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        bookmaker_value = ",".join(bookmakers)
        if bookmaker_value:
            params["bookmakers"] = bookmaker_value
        return await self.http.request_json(
            "GET",
            f"/v4/historical/sports/{self.sport_key}/odds",
            params=params,
        )

    async def historical_event_odds(
        self,
        *,
        event_id: str,
        snapshot_at: datetime,
        regions: str = "us",
        markets: Iterable[str],
        bookmakers: Iterable[str] = (),
    ) -> ProviderResponse:
        params = {
            "apiKey": self.api_key,
            "date": snapshot_at.isoformat().replace("+00:00", "Z"),
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        bookmaker_value = ",".join(bookmakers)
        if bookmaker_value:
            params["bookmakers"] = bookmaker_value
        return await self.http.request_json(
            "GET",
            f"/v4/historical/sports/{self.sport_key}/events/{event_id}/odds",
            params=params,
        )
