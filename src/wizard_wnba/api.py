from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pydantic import BaseModel, Field

from .demo import build_demo_snapshot
from .product import (
    BOOKMAKERS,
    MARKETS,
    PREFERENCES_STORAGE_KEY,
    PUBLIC_ODDS_FORMAT,
)
from .publication import AtomicPublicationStore
from .scenario import ScenarioRequest, run_scenario
from .live_markets import build_live_market_feed
from .logging_redaction import install_log_redaction
from .live_reference import (
    LiveReferenceError,
    simulate_live_reference,
)
from .settings import get_settings
from .worker import heartbeat_age_seconds
from starlette.concurrency import run_in_threadpool


settings = get_settings()
publication_store = AtomicPublicationStore(
    settings.data_dir / "recommendations" / "current.json"
)


class LiveReferenceScenarioRequest(BaseModel):
    market_id: str
    side: str | None = None
    line: float | None = None
    american_odds: int | None = None
    current_total: float | None = Field(default=None, ge=0)
    remaining_minutes: float | None = Field(
        default=None,
        ge=0,
        le=50,
    )
    simulations: int = Field(default=20_000, ge=1_000, le=100_000)




@asynccontextmanager
async def lifespan(_: FastAPI):
    install_log_redaction()
    if not publication_store.path.exists():
        publication_store.write(
            build_demo_snapshot(
                simulations=min(settings.default_simulations, 5000),
                seed=settings.random_seed,
            )
        )
    yield


app = FastAPI(
    title="WizardofOdds.com WNBA In-Play Simulation",
    version="0.2.0",
    description=(
        "Always-on WNBA in-play fair-probability, market-comparison, "
        "conservative-ROI recommendation, preferences, and scenario service."
    ),
    lifespan=lifespan,
    # Empty by default (served at /). When the reverse proxy strips a subpath
    # prefix, set ROOT_PATH so generated URLs (OpenAPI/docs/redirects) are
    # correct. The app also serves the literal public subpath route directly.
    root_path=settings.root_path,
)

static_dir = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _index_file() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return _index_file()


@app.get(
    "/tools/odds-scanner/predictions/WNBA/In-Play/Simulation",
    include_in_schema=False,
)
async def public_simulation_page_no_slash() -> FileResponse:
    return _index_file()


@app.get(
    "/tools/odds-scanner/predictions/WNBA/In-Play/Simulation/",
    include_in_schema=False,
)
async def public_simulation_page() -> FileResponse:
    return _index_file()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def build_health() -> dict[str, Any]:
    """Report true readiness, not merely that Uvicorn answered.

    The service is only healthy when a live worker completed a cycle recently
    (fresh heartbeat) and the published snapshot is neither stale nor
    fail-closed.
    """
    now = datetime.now(UTC)
    snapshot = publication_store.read()
    metrics = snapshot.get("metrics", {}) if isinstance(snapshot, dict) else {}

    # Heartbeat freshness: prefer the worker heartbeat file, fall back to the
    # heartbeat timestamp embedded in the snapshot metrics.
    heartbeat_age = heartbeat_age_seconds(settings.data_dir)
    if heartbeat_age is None:
        beat_at = _parse_iso(metrics.get("worker_heartbeat_at"))
        if beat_at is not None:
            heartbeat_age = max(0.0, (now - beat_at).total_seconds())

    # A heartbeat older than several cycles means the worker has stalled.
    stale_after = max(60.0, settings.game_discovery_seconds * 3.0)
    heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= stale_after

    if heartbeat_age is None:
        worker_status = "NO_HEARTBEAT"
    elif heartbeat_fresh:
        worker_status = str(metrics.get("worker_status") or "HEALTHY")
    else:
        worker_status = "STALE"

    # Snapshot freshness: a snapshot that has not been regenerated recently is
    # rejected even if its contents look fine.
    generated_at = _parse_iso(snapshot.get("generated_at"))
    snapshot_age = (
        max(0.0, (now - generated_at).total_seconds())
        if generated_at is not None
        else None
    )
    snapshot_fresh = snapshot_age is not None and snapshot_age <= stale_after

    engine_status = snapshot.get("engine_status")
    healthy = (
        heartbeat_fresh
        and snapshot_fresh
        and worker_status not in {"STALE", "NO_HEARTBEAT", "DEGRADED"}
        and engine_status not in {"DEGRADED", "STARTING", None}
    )

    return {
        "status": "ok" if healthy else "degraded",
        "healthy": healthy,
        "service": "wizardofodds-wnba-inplay",
        "environment": settings.environment,
        "engine_status": engine_status,
        "data_status": snapshot.get("data_status"),
        "worker_status": worker_status,
        "worker_heartbeat_age_seconds": heartbeat_age,
        "worker_heartbeat_fresh": heartbeat_fresh,
        "worker_cycle": metrics.get("worker_cycle"),
        "snapshot_age_seconds": snapshot_age,
        "snapshot_fresh": snapshot_fresh,
        "staleness_threshold_seconds": stale_after,
        "odds_format": PUBLIC_ODDS_FORMAT,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return build_health()


@app.get("/api/v1/snapshot")
async def snapshot() -> dict[str, Any]:
    return publication_store.read()


@app.get("/api/v1/product-options")
async def product_options() -> dict[str, Any]:
    return {
        "odds_format": PUBLIC_ODDS_FORMAT,
        "bookmakers": list(BOOKMAKERS),
        "markets": list(MARKETS),
        "preferences_storage_key": PREFERENCES_STORAGE_KEY,
        "minimum_conservative_roi": settings.hard_min_conservative_roi,
        "preferences_scope": "this_browser",
        "scenario_lab_official": False,
    }


@app.get("/api/v1/live-markets")
async def live_markets() -> dict[str, Any]:
    current = publication_store.read()
    return build_live_market_feed(
        settings.data_dir,
        list(current.get("games", [])),
    )


@app.get("/api/v1/recommendations")
async def recommendations(
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    bookmaker: list[str] | None = Query(default=None),
    min_conservative_roi: float = Query(default=0.0, ge=0.0),
    max_market_age_seconds: float | None = Query(default=None, ge=0.0),
    min_grade: str = Query(default="WATCH", pattern="^(WATCH|C|B|A)$"),
) -> dict[str, Any]:
    snapshot_value = publication_store.read()
    items = snapshot_value.get("recommendations", [])
    grade_rank = {"WATCH": 0, "C": 1, "B": 2, "A": 3}

    if status:
        items = [
            item
            for item in items
            if item.get("status") == status.upper()
        ]
    if market:
        items = [
            item
            for item in items
            if item.get("market_key") == market
        ]
    if bookmaker:
        selected_books = set(bookmaker)
        items = [
            item
            for item in items
            if item.get("bookmaker_key") in selected_books
        ]
    if max_market_age_seconds is not None:
        items = [
            item
            for item in items
            if float(item.get("market_age_seconds", float("inf")))
            <= max_market_age_seconds
        ]

    items = [
        item
        for item in items
        if float(item.get("conservative_roi", -1))
        >= min_conservative_roi
        and grade_rank.get(
            str(item.get("confidence_grade", "WATCH")),
            0,
        )
        >= grade_rank[min_grade]
    ]
    return {
        "generated_at": snapshot_value.get("generated_at"),
        "odds_format": PUBLIC_ODDS_FORMAT,
        "count": len(items),
        "recommendations": items,
    }


@app.post("/api/v1/live-reference-simulation")
async def live_reference_simulation(
    request: LiveReferenceScenarioRequest,
) -> dict[str, Any]:
    snapshot_value = publication_store.read()
    market_feed = build_live_market_feed(
        settings.data_dir,
        list(snapshot_value.get("games", [])),
    )
    try:
        return await run_in_threadpool(lambda: simulate_live_reference(
            data_dir=settings.data_dir,
            snapshot=snapshot_value,
            market_feed=market_feed,
            market_id=request.market_id,
            side=request.side,
            line=request.line,
            american_odds=request.american_odds,
            current_total=request.current_total,
            remaining_minutes=request.remaining_minutes,
            simulations=request.simulations,
            seed=settings.random_seed,
        ))
    except LiveReferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/scenario-lab")
async def scenario_lab(request: ScenarioRequest) -> dict[str, Any]:
    snapshot_value = publication_store.read()
    recommendation = next(
        (
            item
            for item in snapshot_value.get("recommendations", [])
            if item.get("recommendation_id")
            == request.recommendation_id
        ),
        None,
    )
    if recommendation is None:
        raise HTTPException(
            status_code=404,
            detail="Recommendation is not present in the current snapshot",
        )

    try:
        result = run_scenario(recommendation, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


@app.get("/api/v1/model-card")
async def model_card() -> dict[str, Any]:
    return {
        "product": "WizardofOdds.com WNBA In-Play Simulation",
        "objective": (
            "Maximize out-of-sample expected ROI subject to probability "
            "calibration, uncertainty, freshness, and integrity gates."
        ),
        "policy": {
            "mode": "adaptive",
            "hard_min_conservative_roi":
                settings.hard_min_conservative_roi,
            "publication_requires_calibration": True,
            "inventory_shading": False,
            "odds_format": PUBLIC_ODDS_FORMAT,
        },
        "features": {
            "always_on_public_page": True,
            "browser_preferences": True,
            "browser_notifications": True,
            "scenario_lab": True,
            "bovada_supported": True,
        },
        "limitations": [
            "Probabilities are estimates, not guarantees.",
            "Availability and prices can change before a user acts.",
            "Scenario Lab results are user-defined and not official picks.",
            "Demo profiles must be replaced by trained WNBA model bundles.",
        ],
    }


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    previous = ""
    try:
        while True:
            payload = publication_store.read()
            rendered = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            if rendered != previous:
                await websocket.send_text(rendered)
                previous = rendered
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


def run() -> None:
    uvicorn.run(
        "wizard_wnba.api:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        root_path=get_settings().root_path,
    )


if __name__ == "__main__":
    run()
