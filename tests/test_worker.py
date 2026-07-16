from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from wizard_wnba import worker
from wizard_wnba.adapters.http import AsyncProviderClient, ProviderHTTPError
from wizard_wnba.adapters.the_odds_api import (
    EventOddsUnavailable,
    TheOddsApiClient,
)
from wizard_wnba.collector import CollectionCycle
from wizard_wnba.live_engine import EngineResult
from wizard_wnba.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        BALLDONTLIE_API_KEY="bdl-key",
        THE_ODDS_API_KEY="odds-key",
        DATA_DIR=str(tmp_path),
        LIVE_ENABLED=True,
        GAME_DISCOVERY_SECONDS=0.01,
        ENVIRONMENT="test",
    )


def _cycle(*, errors=(), skipped_events=()) -> CollectionCycle:
    return CollectionCycle(
        captured_at=datetime.now(UTC),
        bdl_games_payload={"data": []},
        odds_events_payload=(),
        featured_odds_payload=(),
        event_odds_payloads={},
        live_game_payloads={},
        quota_remaining=100,
        errors=tuple(errors),
        skipped_events=tuple(skipped_events),
    )


class FakeCollector:
    def __init__(self, stop: asyncio.Event, *, cycles, results):
        self.stop = stop
        self.cycles = cycles
        self.results = results
        self.count = 0
        self.closed = False

    async def collect_once(self) -> CollectionCycle:
        self.count += 1
        if self.count >= self.cycles:
            # Ask the loop to stop after this cycle completes.
            self.stop.set()
        return self.results[min(self.count - 1, len(self.results) - 1)]

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self, result: EngineResult):
        self.result = result
        self.calls = 0

    def process(self, cycle) -> EngineResult:
        self.calls += 1
        return self.result


def _install(monkeypatch, tmp_path, *, collector_factory, engine_result):
    settings = _settings(tmp_path)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "BallDontLieClient", lambda **k: object())
    monkeypatch.setattr(worker, "TheOddsApiClient", lambda **k: object())
    monkeypatch.setattr(worker, "IdentityRegistry", lambda *a, **k: object())
    monkeypatch.setattr(
        worker,
        "LiveRecommendationEngine",
        lambda *a, **k: FakeEngine(engine_result),
    )
    monkeypatch.setattr(worker, "LiveCollector", collector_factory)
    return settings


def _current(settings: Settings) -> dict:
    path = settings.data_dir / "recommendations" / "current.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_worker_survives_five_cycles_healthy(monkeypatch, tmp_path):
    stop = asyncio.Event()

    # Every cycle: an event 404 was isolated by the collector (skipped_events,
    # NOT errors); the engine had nonfatal skips (missing profile, unmatched
    # game) and produced zero recommendations.
    cycle = _cycle(errors=(), skipped_events=("evt-404",))
    result = EngineResult(
        recommendations=(),
        games=(),
        errors=(),
        skips=("PLAYER_PROFILES_MISSING:1:p", "ODDS_EVENT_UNMATCHED:2"),
    )

    def collector_factory(*a, **k):
        return FakeCollector(stop, cycles=5, results=[cycle])

    settings = _install(
        monkeypatch,
        tmp_path,
        collector_factory=collector_factory,
        engine_result=result,
    )

    await asyncio.wait_for(worker.live_loop(stop), timeout=10)

    snapshot = _current(settings)
    metrics = snapshot["metrics"]

    # 5 complete cycles ran and the snapshot reflects the last one.
    assert metrics["worker_cycle"] == 5
    # Zero recommendations is a HEALTHY no-bet state.
    assert snapshot["engine_status"] == "HEALTHY"
    assert (
        snapshot["data_status"]
        == "LIVE_COLLECTION_ACTIVE_NO_QUALIFYING_RECOMMENDATIONS"
    )
    assert (
        snapshot["data_status"]
        != "LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED"
    )
    assert metrics["collector_errors"] == []
    assert metrics["engine_errors"] == []
    # The 404 shows up only as a nonfatal skip.
    assert metrics["collector_skipped_events"] == ["evt-404"]

    # Heartbeat is fresh.
    age = worker.heartbeat_age_seconds(settings.data_dir)
    assert age is not None and age < 5.0
    beat = worker.read_heartbeat(settings.data_dir)
    assert beat["cycle"] == 5
    assert beat["status"] == "HEALTHY"
    assert worker.healthcheck_exit_code() == 0


@pytest.mark.asyncio
async def test_worker_fatal_engine_error_degrades_but_survives(
    monkeypatch, tmp_path
):
    stop = asyncio.Event()
    cycle = _cycle()
    result = EngineResult(
        recommendations=(),
        games=(),
        errors=("SIMULATION_FAILED:1:boom",),
        skips=(),
    )

    def collector_factory(*a, **k):
        return FakeCollector(stop, cycles=5, results=[cycle])

    settings = _install(
        monkeypatch,
        tmp_path,
        collector_factory=collector_factory,
        engine_result=result,
    )

    await asyncio.wait_for(worker.live_loop(stop), timeout=10)

    snapshot = _current(settings)
    # Worker completed all 5 cycles despite the fatal engine error each time.
    assert snapshot["metrics"]["worker_cycle"] == 5
    assert snapshot["engine_status"] == "DEGRADED"
    assert (
        snapshot["data_status"]
        == "LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED"
    )
    assert snapshot["metrics"]["engine_errors"] == ["SIMULATION_FAILED:1:boom"]


@pytest.mark.asyncio
async def test_worker_survives_total_collection_failure(monkeypatch, tmp_path):
    stop = asyncio.Event()

    class ExplodingCollector:
        def __init__(self):
            self.count = 0
            self.closed = False

        async def collect_once(self):
            self.count += 1
            if self.count >= 5:
                stop.set()
            raise RuntimeError("provider outage")

        async def close(self):
            self.closed = True

    def collector_factory(*a, **k):
        return ExplodingCollector()

    settings = _install(
        monkeypatch,
        tmp_path,
        collector_factory=collector_factory,
        engine_result=EngineResult((), (), (), ()),
    )

    await asyncio.wait_for(worker.live_loop(stop), timeout=10)

    snapshot = _current(settings)
    assert snapshot["metrics"]["worker_cycle"] == 5
    assert snapshot["engine_status"] == "DEGRADED"
    assert any(
        "COLLECTION_FAILED" in e for e in snapshot["metrics"]["collector_errors"]
    )


# --- Event-odds 404: nonfatal, not retried, not escalated -------------------


@pytest.mark.asyncio
async def test_http_client_does_not_retry_404(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://example.test", transport=transport
    ) as inner:
        client = AsyncProviderClient(
            provider="the_odds_api",
            base_url="https://example.test",
            client=inner,
        )
        with pytest.raises(ProviderHTTPError) as excinfo:
            await client.request_json("GET", "/v4/thing")

    assert excinfo.value.status_code == 404
    # Fired exactly once — a 404 is deterministic and is NOT retried 4 times.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_http_client_retries_5xx(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://example.test", transport=transport
    ) as inner:
        client = AsyncProviderClient(
            provider="p",
            base_url="https://example.test",
            client=inner,
            max_attempts=4,
        )
        with pytest.raises(Exception):
            await client.request_json("GET", "/v4/thing")

    # Persistent 5xx IS retried up to max_attempts.
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_event_odds_404_raises_event_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "event not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://api.the-odds-api.com", transport=transport
    ) as inner:
        provider = AsyncProviderClient(
            provider="the_odds_api",
            base_url="https://api.the-odds-api.com",
            client=inner,
        )
        odds = TheOddsApiClient(api_key="k", client=provider)
        with pytest.raises(EventOddsUnavailable) as excinfo:
            await odds.event_odds(event_id="evt-1", markets=("h2h",))
    assert excinfo.value.event_id == "evt-1"
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_collector_isolates_event_404_as_skip(tmp_path):
    from wizard_wnba.collector import LiveCollector
    from wizard_wnba.storage import RawSnapshotStore

    settings = _settings(tmp_path)

    class FakeBDL:
        async def games(self, **k):
            return _Resp({"data": []})

        async def close(self):
            pass

    class FakeOdds:
        async def events(self):
            return _Resp([{"id": "evt-1"}, {"id": "evt-2"}])

        async def featured_odds(self, **k):
            return _Resp([])

        async def event_odds(self, *, event_id, **k):
            if event_id == "evt-1":
                raise EventOddsUnavailable(event_id)
            return _Resp([{"id": event_id, "bookmakers": []}])

        async def close(self):
            pass

    collector = LiveCollector(
        settings,
        bdl=FakeBDL(),
        odds=FakeOdds(),
        snapshot_store=RawSnapshotStore(settings.data_dir / "raw"),
    )
    cycle = await collector.collect_once()

    # The 404 event is a nonfatal skip, never a fatal collector error.
    assert cycle.errors == ()
    assert cycle.skipped_events == ("evt-1",)
    assert "evt-2" in cycle.event_odds_payloads


class _Resp:
    provider = "the_odds_api"
    endpoint = "/x"
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self.payload = payload
