from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


class SportsdataverseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SportsdataverseResult:
    function_name: str
    payload: Any


class SportsdataverseAdapter:
    """Optional sportsdataverse.wnba 0.0.70 adapter.

    Calls run in a worker thread because the package functions are synchronous.
    The adapter keeps this optional dependency outside the core live process.
    """

    def __init__(self) -> None:
        try:
            import sportsdataverse.wnba as wnba
        except ImportError as exc:
            raise SportsdataverseUnavailable(
                "Install the 'sportsdataverse' extra to enable ESPN reconciliation"
            ) from exc
        self.wnba = wnba

    async def call(self, function_name: str, **kwargs: Any) -> SportsdataverseResult:
        function: Callable[..., Any] | None = getattr(self.wnba, function_name, None)
        if function is None:
            raise AttributeError(f"unknown sportsdataverse WNBA function: {function_name}")
        payload = await asyncio.to_thread(function, **kwargs)
        return SportsdataverseResult(function_name=function_name, payload=payload)

    async def game_play(self, game_id: int | str) -> SportsdataverseResult:
        return await self.call("espn_wnba_game_play", game_id=game_id)

    async def game_play_personnel(self, game_id: int | str) -> SportsdataverseResult:
        return await self.call("espn_wnba_game_play_personnel", game_id=game_id)

    async def game_situation(self, game_id: int | str) -> SportsdataverseResult:
        return await self.call("espn_wnba_game_situation", game_id=game_id)

    async def game_roster(self, game_id: int | str) -> SportsdataverseResult:
        return await self.call("espn_wnba_game_team_roster", game_id=game_id)
