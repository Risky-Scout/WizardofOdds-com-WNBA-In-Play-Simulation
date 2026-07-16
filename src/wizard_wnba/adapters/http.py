from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
from typing import Any, Mapping

import httpx


def _short_body(response: httpx.Response) -> str:
    try:
        return response.text[:200]
    except Exception:  # pragma: no cover - defensive
        return ""


class ProviderError(RuntimeError):
    pass


class ProviderHTTPError(ProviderError):
    """A provider returned a non-retryable HTTP status.

    Carries ``status_code`` so callers can distinguish an expected,
    resource-specific 404 (a removed event) from fatal auth/quota/server
    failures. Client errors (4xx other than 429) are never retried because
    replaying an identical request cannot change the outcome.
    """

    def __init__(
        self,
        *,
        provider: str,
        method: str,
        path: str,
        status_code: int,
        message: str = "",
    ) -> None:
        self.provider = provider
        self.method = method
        self.path = path
        self.status_code = status_code
        detail = f": {message}" if message else ""
        super().__init__(
            f"{provider} {method} {path} returned HTTP {status_code}{detail}"
        )


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
            except httpx.HTTPError as exc:
                # Transport/network failures are transient: retry with backoff.
                last_error = exc
                if attempt == self.max_attempts:
                    break
                await self._sleep_backoff(attempt)
                continue

            status = response.status_code

            if status == 429:
                # Rate limited: honor Retry-After and try again (fatal only
                # after attempts are exhausted).
                retry_after = self._retry_after_seconds(response)
                last_error = ProviderHTTPError(
                    provider=self.provider,
                    method=method,
                    path=path,
                    status_code=status,
                    message="rate limited",
                )
                if attempt == self.max_attempts:
                    break
                await asyncio.sleep(retry_after)
                continue

            if 400 <= status < 500:
                # Client errors (401/403/404/...) are deterministic. Replaying
                # the identical request cannot change the result, so fail fast
                # with the status code preserved instead of retrying four times.
                raise ProviderHTTPError(
                    provider=self.provider,
                    method=method,
                    path=path,
                    status_code=status,
                    message=_short_body(response),
                )

            if status >= 500:
                # Server errors may be transient: retry with backoff.
                last_error = ProviderHTTPError(
                    provider=self.provider,
                    method=method,
                    path=path,
                    status_code=status,
                    message=_short_body(response),
                )
                if attempt == self.max_attempts:
                    break
                await self._sleep_backoff(attempt)
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                # An invalid/partial JSON body is treated as transient.
                last_error = exc
                if attempt == self.max_attempts:
                    break
                await self._sleep_backoff(attempt)
                continue

            return ProviderResponse(
                provider=self.provider,
                endpoint=path,
                status_code=status,
                headers=dict(response.headers),
                payload=payload,
                elapsed_ms=response.elapsed.total_seconds() * 1000,
            )

        raise ProviderError(
            f"{self.provider} request failed after {self.max_attempts} attempts: "
            f"{method} {path}: {last_error}"
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        delay = min(0.25 * 2 ** (attempt - 1), 3.0)
        delay *= random.uniform(0.8, 1.2)
        await asyncio.sleep(delay)

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        try:
            return min(float(response.headers.get("retry-after", "1")), 10.0)
        except (TypeError, ValueError):
            return 1.0
