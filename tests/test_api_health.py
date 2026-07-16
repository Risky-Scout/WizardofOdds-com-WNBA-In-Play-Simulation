from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

from fastapi.testclient import TestClient
import pytest

from wizard_wnba import api, worker
from wizard_wnba.publication import recommendation_snapshot

DATA_DIR = api.settings.data_dir
CURRENT = DATA_DIR / "recommendations" / "current.json"
HEARTBEAT = worker.heartbeat_path(DATA_DIR)


def _write_snapshot(*, engine_status="HEALTHY", data_status="LIVE", age_s=0.0,
                    worker_status="HEALTHY", cycle=7, include_heartbeat_meta=True):
    CURRENT.parent.mkdir(parents=True, exist_ok=True)
    extra = {"worker_status": worker_status, "worker_cycle": cycle}
    if include_heartbeat_meta:
        extra["worker_heartbeat_at"] = datetime.now(UTC).isoformat()
    snapshot = recommendation_snapshot(
        [],
        environment="test",
        engine_status=engine_status,
        data_status=data_status,
        games=[],
        extra_metrics=extra,
    ).to_dict()
    if age_s:
        snapshot["generated_at"] = (
            datetime.now(UTC) - timedelta(seconds=age_s)
        ).isoformat()
    CURRENT.write_text(json.dumps(snapshot), encoding="utf-8")


def _write_heartbeat(*, age_s=0.0, status="HEALTHY", cycle=7):
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(
        json.dumps(
            {
                "updated_at": (
                    datetime.now(UTC) - timedelta(seconds=age_s)
                ).isoformat(),
                "cycle": cycle,
                "status": status,
                "mode": "live",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clean():
    for path in (CURRENT, HEARTBEAT):
        if path.exists():
            path.unlink()
    yield
    for path in (CURRENT, HEARTBEAT):
        if path.exists():
            path.unlink()


def test_health_degraded_without_worker_heartbeat():
    _write_snapshot(include_heartbeat_meta=False)
    payload = api.build_health()
    assert payload["status"] == "degraded"
    assert payload["healthy"] is False
    assert payload["worker_status"] == "NO_HEARTBEAT"
    assert payload["worker_heartbeat_age_seconds"] is None


def test_health_ok_with_fresh_heartbeat_and_snapshot():
    _write_snapshot(engine_status="HEALTHY")
    _write_heartbeat(age_s=1.0, status="HEALTHY")
    payload = api.build_health()
    assert payload["status"] == "ok"
    assert payload["healthy"] is True
    assert payload["worker_status"] == "HEALTHY"
    assert payload["worker_heartbeat_fresh"] is True
    assert payload["worker_heartbeat_age_seconds"] < 5.0
    assert payload["worker_cycle"] == 7
    # It reports engine + data status too.
    assert payload["engine_status"] == "HEALTHY"


def test_health_degraded_when_heartbeat_stale():
    _write_snapshot()
    _write_heartbeat(age_s=10_000, status="HEALTHY")
    payload = api.build_health()
    assert payload["status"] == "degraded"
    assert payload["worker_status"] == "STALE"
    assert payload["worker_heartbeat_fresh"] is False


def test_health_degraded_when_snapshot_stale():
    _write_snapshot(age_s=10_000)
    _write_heartbeat(age_s=1.0)
    payload = api.build_health()
    assert payload["snapshot_fresh"] is False
    assert payload["status"] == "degraded"


def test_health_not_ok_merely_because_uvicorn_answers():
    # No worker, no snapshot at all -> route answers 200 but reports degraded.
    with TestClient(api.app) as client:
        # Lifespan may write a demo snapshot; remove the heartbeat to be sure.
        if HEARTBEAT.exists():
            HEARTBEAT.unlink()
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "worker_heartbeat_age_seconds" in body
    assert "worker_status" in body
    assert body["status"] == "degraded"


def test_live_simulation_preserves_exact_market_id(monkeypatch):
    captured = {}

    def fake_sim(**kwargs):
        captured["market_id"] = kwargs["market_id"]
        return {"market_id": kwargs["market_id"], "engine_status": "OK"}

    monkeypatch.setattr(api, "simulate_live_reference", fake_sim)
    _write_snapshot()

    with TestClient(api.app) as client:
        response = client.post(
            "/api/v1/live-reference-simulation",
            json={"market_id": "betmgm-h2h-exact-id", "simulations": 2000},
        )
    assert response.status_code == 200
    assert captured["market_id"] == "betmgm-h2h-exact-id"
    assert response.json()["market_id"] == "betmgm-h2h-exact-id"
