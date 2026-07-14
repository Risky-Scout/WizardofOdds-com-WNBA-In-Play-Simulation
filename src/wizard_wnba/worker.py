from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .adapters.balldontlie import BallDontLieClient
from .adapters.the_odds_api import TheOddsApiClient
from .collector import LiveCollector
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

    try:
        while not stop.is_set():
            cycle = await collector.collect_once()
            recommendations, games, engine_errors = engine.process(cycle)
            all_errors = tuple(cycle.errors) + tuple(engine_errors)

            if all_errors:
                engine_status = "DEGRADED"
                data_status = (
                    "LIVE_COLLECTION_ACTIVE_PUBLICATION_FAIL_CLOSED"
                )
            else:
                engine_status = "HEALTHY"
                data_status = "LIVE"

            # The snapshot is rebuilt from the current cycle. No demo or
            # previous-run recommendation can leak into live publication.
            publication.write(
                recommendation_snapshot(
                    recommendations,
                    environment=settings.environment,
                    engine_status=engine_status,
                    data_status=data_status,
                    games=games,
                    extra_metrics={
                        "bdl_games": cycle.bdl_games,
                        "odds_events": cycle.odds_events,
                        "in_progress_games": cycle.in_progress_games,
                        "odds_quota_remaining": cycle.quota_remaining,
                        "collector_errors": list(cycle.errors),
                        "engine_errors": list(engine_errors),
                        "simulation_count": settings.default_simulations,
                        "policy_mode": settings.policy_mode,
                    },
                )
            )

            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=settings.game_discovery_seconds,
                )
            except TimeoutError:
                pass
    finally:
        await collector.close()


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


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("demo", "live"),
        default="live" if get_settings().live_enabled else "demo",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main(args.mode))


if __name__ == "__main__":
    run()
