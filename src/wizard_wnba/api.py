from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .demo import build_demo_snapshot
from .publication import AtomicPublicationStore
from .settings import get_settings


settings = get_settings()
publication_store = AtomicPublicationStore(
    settings.data_dir / "recommendations" / "current.json"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    version="0.1.0",
    description=(
        "Automated WNBA in-play fair-probability, market-comparison, and "
        "conservative-ROI recommendation service."
    ),
    lifespan=lifespan,
)

static_dir = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    snapshot = publication_store.read()
    return {
        "status": "ok",
        "service": "wizardofodds-wnba-inplay",
        "environment": settings.environment,
        "engine_status": snapshot.get("engine_status"),
        "data_status": snapshot.get("data_status"),
    }


@app.get("/api/v1/snapshot")
async def snapshot() -> dict[str, Any]:
    return publication_store.read()


@app.get("/api/v1/recommendations")
async def recommendations(
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    min_conservative_roi: float = Query(default=0.0),
) -> dict[str, Any]:
    snapshot_value = publication_store.read()
    items = snapshot_value.get("recommendations", [])
    if status:
        items = [item for item in items if item.get("status") == status.upper()]
    if market:
        items = [item for item in items if item.get("market_key") == market]
    items = [
        item
        for item in items
        if float(item.get("conservative_roi", -1)) >= min_conservative_roi
    ]
    return {
        "generated_at": snapshot_value.get("generated_at"),
        "count": len(items),
        "recommendations": items,
    }


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
            "hard_min_conservative_roi": settings.hard_min_conservative_roi,
            "publication_requires_calibration": True,
            "inventory_shading": False,
        },
        "limitations": [
            "Probabilities are estimates, not guarantees.",
            "Availability and prices can change before a user acts.",
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
            rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
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
    )


if __name__ == "__main__":
    run()
