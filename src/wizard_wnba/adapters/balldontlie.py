from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .http import AsyncProviderClient, ProviderResponse


class BallDontLieClient:
    """BALLDONTLIE WNBA adapter.

    Authentication uses the Authorization header. Raw responses should always
    be archived before normalization.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.balldontlie.io",
        client: AsyncProviderClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BALLDONTLIE API key is required")
        self.api_key = api_key
        self.http = client or AsyncProviderClient(
            provider="balldontlie",
            base_url=base_url,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key}

    async def close(self) -> None:
        await self.http.close()

    async def games(
        self,
        *,
        dates: Iterable[date] = (),
        game_ids: Iterable[int] = (),
        per_page: int = 100,
    ) -> ProviderResponse:
        params: list[tuple[str, Any]] = [("per_page", min(per_page, 100))]
        params.extend(("dates[]", value.isoformat()) for value in dates)
        params.extend(("game_ids[]", value) for value in game_ids)
        return await self.http.request_json(
            "GET",
            "/wnba/v1/games",
            params=params,
            headers=self.headers,
        )

    async def plays(self, game_id: int) -> ProviderResponse:
        return await self.http.request_json(
            "GET",
            "/wnba/v1/plays",
            params={"game_id": game_id},
            headers=self.headers,
        )

    async def player_stats(
        self,
        *,
        game_ids: Iterable[int],
        per_page: int = 100,
    ) -> ProviderResponse:
        params: list[tuple[str, Any]] = [("per_page", min(per_page, 100))]
        params.extend(("game_ids[]", value) for value in game_ids)
        return await self.http.request_json(
            "GET",
            "/wnba/v1/player_stats",
            params=params,
            headers=self.headers,
        )

    async def injuries(self) -> ProviderResponse:
        return await self.http.request_json(
            "GET",
            "/wnba/v1/player_injuries",
            headers=self.headers,
        )

    async def game_odds(
        self,
        *,
        game_ids: Iterable[int] = (),
        dates: Iterable[date] = (),
        vendors: Iterable[str] = (),
    ) -> ProviderResponse:
        params: list[tuple[str, Any]] = []
        params.extend(("game_ids[]", value) for value in game_ids)
        params.extend(("dates[]", value.isoformat()) for value in dates)
        params.extend(("vendors[]", value) for value in vendors)
        return await self.http.request_json(
            "GET",
            "/wnba/v1/odds",
            params=params,
            headers=self.headers,
        )

    async def player_props(
        self,
        *,
        game_id: int,
        vendors: Iterable[str] = (),
    ) -> ProviderResponse:
        params: list[tuple[str, Any]] = [("game_id", game_id)]
        params.extend(("vendors[]", value) for value in vendors)
        return await self.http.request_json(
            "GET",
            "/wnba/v1/odds/player_props",
            params=params,
            headers=self.headers,
        )
