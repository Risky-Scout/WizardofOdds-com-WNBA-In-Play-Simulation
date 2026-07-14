from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
from typing import Any, Mapping

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    endpoint: str
    status_code: int
    headers: Mapping[str, str]
    payload: Any
    elapsed_ms: float


class AsyncProviderClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        timeout_seconds: float = 10.0,
        max_attempts: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ProviderResponse:
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.client.request(
                    method,
                    path,
                    params=params,
                    headers=headers,
                )
                if response.status_code == 429:
                    retry_after = float(response.headers.get("retry-after", "1"))
                    await asyncio.sleep(min(retry_after, 10.0))
                    continue
                response.raise_for_status()
                return ProviderResponse(
                    provider=self.provider,
                    endpoint=path,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    payload=response.json(),
                    elapsed_ms=response.elapsed.total_seconds() * 1000,
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    break
                delay = min(0.25 * 2 ** (attempt - 1), 3.0)
                delay *= random.uniform(0.8, 1.2)
                await asyncio.sleep(delay)

        raise ProviderError(
            f"{self.provider} request failed after {self.max_attempts} attempts: "
            f"{method} {path}: {last_error}"
        )
