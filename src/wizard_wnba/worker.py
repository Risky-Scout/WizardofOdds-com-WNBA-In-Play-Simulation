from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import signal
import tempfile

from .adapters.balldontlie import BallDontLieClient
from .adapters.the_odds_api import TheOddsApiClient
from .collector import LiveCollector
from .logging_redaction import install_log_redaction
from .demo import build_demo_snapshot
from .identity import IdentityRegistry
from .live_engine import LiveRecommendationEngine
from .publication import (
    AtomicPublicationStore,
    recommendation_snapshot,
)
from .settings import get_settings
from .storage import RawSnapshotStore


logger = logging.getLogger(__name__)


HEARTBEAT_FILENAME = "worker_heartbeat.json"


def heartbeat_path(data_dir: Path) -> Path:
    return data_dir / HEARTBEAT_FILENAME


def write_heartbeat(
    data_dir: Path,
    *,
    cycle: int,
    status: str,
    mode: str,
    detail: str = "",
) -> str:
    """Atomically record a worker heartbeat and return its ISO timestamp.

    A fresh heartbeat is the single source of truth for "the worker completed a
    cycle recently". /health and the docker health check both read it, so a
    live Uvicorn process alone can never look fully healthy.
    """
    now = datetime.now(UTC).isoformat()
    payload = {
        "updated_at": now,
        "cycle": cycle,
        "status": status,
        "mode": mode,
        "detail": detail,
        "pid": os.getpid(),
    }
    path = heartbeat_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return now


def read_heartbeat(data_dir: Path) -> dict | None:
    path = heartbeat_path(data_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_age_seconds(data_dir: Path) -> float | None:
    beat = read_heartbeat(data_dir)
    if not beat:
        return None
    try:
        updated = datetime.fromisoformat(str(beat["updated_at"]))
    except (KeyError, ValueError):
        return None
    return max(0.0, (datetime.now(UTC) - updated).total_seconds())


async def demo_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    store = AtomicPublicationStore(
        settings.data_dir / "recommendations" / "current.json"
    )
    cycle = 0
    while not stop.is_set():
        store.write(
            build_demo_snapshot(
                simulations=min(settings.default_simulations, 5000),
                seed=settings.random_seed + cycle,
            )
        )
        cycle += 1
        write_heartbeat(
            settings.data_dir,
            cycle=cycle,
            status="HEALTHY",
            mode="demo",
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except TimeoutError:
            pass


async def live_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    if not settings.balldontlie_api_key:
        raise RuntimeError("BALLDONTLIE_API_KEY is not configured")
    if not settings.the_odds_api_key:
        raise RuntimeError("THE_ODDS_API_KEY is not configured")

    collector = LiveCollector(
        settings,
        bdl=BallDontLieClient(
            api_key=settings.balldontlie_api_key,
            base_url=settings.balldontlie_base_url,
        ),
        odds=TheOddsApiClient(
            api_key=settings.the_odds_api_key,
            base_url=settings.the_odds_api_base_url,
            sport_key=settings.the_odds_api_sport_key,
        ),
        snapshot_store=RawSnapshotStore(settings.data_dir / "raw"),
    )
    publication = AtomicPublicationStore(
        settings.data_dir / "recommendations" / "current.json"
    )
    engine = LiveRecommendationEngine(
        settings,
        identity=IdentityRegistry(
            settings.data_dir / "models" / "player_crosswalk.json"
        ),
        model_bundle_path=settings.data_dir / "models" / "production.json",
    )

    cycle_id = 0
    try:
        while not stop.is_set():
            cycle_id += 1

            # Every cycle rebuilds all state from scratch. Nothing from a prior
            # cycle (errors, recommendations, snapshot) can leak forward.
            recommendations: tuple = ()
            games: tuple = ()
            engine_errors: list[str] = []
            engine_skips: list[str] = []
            collector_errors: list[str] = []
            collector_skips: list[str] = []

            try:
                cycle = await collector.collect_once()
            except Exception as exc:
                # A total collection failure is fatal for this cycle. Record it,
                # publish a fail-closed snapshot, heartbeat, and keep looping so
                # the worker survives to the next cycle.
                logger.exception("Collection failed on cycle %s", cycle_id)
                collector_errors = [f"COLLECTION_FAILED: {exc}"]
                _publish_cycle(
                    publication,
                    settings=settings,
                    recommendations=(),
                    games=(),
                    engine_status="DEGRADED",
                    data_status="LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED",
                    cycle_id=cycle_id,
                    metrics={
                        "collector_errors": collector_errors,
                        "engine_errors": [],
                        "collector_skipped_events": [],
                        "engine_skips": [],
                    },
                )
                write_heartbeat(
                    settings.data_dir,
                    cycle=cycle_id,
                    status="DEGRADED",
                    mode="live",
                    detail="collection_failed",
                )
                await _sleep_until_next_cycle(stop, settings)
                continue

            # Event-odds 404s are already isolated as nonfatal skips by the
            # collector, so cycle.errors holds only genuine fatal collector
            # failures.
            collector_errors = list(cycle.errors)
            collector_skips = list(cycle.skipped_events)

            result = engine.process(cycle)
            recommendations = result.recommendations
            games = result.games
            engine_errors = list(result.errors)
            engine_skips = list(result.skips)

            fatal = bool(collector_errors) or bool(engine_errors)
            if fatal:
                engine_status = "DEGRADED"
                data_status = "LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED"
            else:
                # Zero qualifying recommendations is a valid, HEALTHY no-bet
                # state — not a failure.
                engine_status = "HEALTHY"
                data_status = (
                    "LIVE"
                    if recommendations
                    else "LIVE_COLLECTION_ACTIVE_NO_QUALIFYING_RECOMMENDATIONS"
                )

            heartbeat_at = write_heartbeat(
                settings.data_dir,
                cycle=cycle_id,
                status=engine_status,
                mode="live",
            )

            # The snapshot is rebuilt from the current cycle and written
            # atomically only after the cycle has fully completed.
            _publish_cycle(
                publication,
                settings=settings,
                recommendations=recommendations,
                games=games,
                engine_status=engine_status,
                data_status=data_status,
                cycle_id=cycle_id,
                heartbeat_at=heartbeat_at,
                metrics={
                    "bdl_games": cycle.bdl_games,
                    "odds_events": cycle.odds_events,
                    "in_progress_games": cycle.in_progress_games,
                    "odds_quota_remaining": cycle.quota_remaining,
                    "collector_errors": collector_errors,
                    "collector_skipped_events": collector_skips,
                    "engine_errors": engine_errors,
                    "engine_skips": engine_skips,
                    "simulation_count": settings.default_simulations,
                    "policy_mode": settings.policy_mode,
                },
            )

            await _sleep_until_next_cycle(stop, settings)
    finally:
        await collector.close()


def _publish_cycle(
    publication: AtomicPublicationStore,
    *,
    settings,
    recommendations,
    games,
    engine_status: str,
    data_status: str,
    cycle_id: int,
    metrics: dict,
    heartbeat_at: str | None = None,
) -> None:
    enriched = dict(metrics)
    enriched["worker_cycle"] = cycle_id
    enriched["worker_status"] = engine_status
    enriched["worker_heartbeat_at"] = (
        heartbeat_at or datetime.now(UTC).isoformat()
    )
    publication.write(
        recommendation_snapshot(
            recommendations,
            environment=settings.environment,
            engine_status=engine_status,
            data_status=data_status,
            games=games,
            extra_metrics=enriched,
        )
    )


async def _sleep_until_next_cycle(stop: asyncio.Event, settings) -> None:
    try:
        await asyncio.wait_for(
            stop.wait(),
            timeout=settings.game_discovery_seconds,
        )
    except TimeoutError:
        pass


async def main(mode: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    if mode == "live":
        await live_loop(stop)
    else:
        await demo_loop(stop)


def healthcheck_exit_code(max_age_seconds: float | None = None) -> int:
    """Return 0 if the worker heartbeat is fresh, 1 otherwise.

    Used by the container health check so the worker is only reported healthy
    when it has actually completed a recent cycle — a live process that has
    stalled fails the check.
    """
    settings = get_settings()
    threshold = (
        max_age_seconds
        if max_age_seconds is not None
        else max(60.0, settings.game_discovery_seconds * 3.0)
    )
    age = heartbeat_age_seconds(settings.data_dir)
    if age is None:
        return 1
    return 0 if age <= threshold else 1


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("demo", "live"),
        default="live" if get_settings().live_enabled else "demo",
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Exit 0 if the worker heartbeat is fresh, else 1.",
    )
    args = parser.parse_args()
    if args.healthcheck:
        raise SystemExit(healthcheck_exit_code())
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_log_redaction()
    asyncio.run(main(args.mode))


if __name__ == "__main__":
    run()
