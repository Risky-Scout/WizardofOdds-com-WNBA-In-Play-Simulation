from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Any, Mapping

from .collector import CollectionCycle
from .domain import PlayerRateProfile, Recommendation
from .identity import IdentityRegistry, normalize_name
from .live_normalization import (
    normalize_bdl_game,
    normalize_bdl_player_stats,
)
from .model_bundle import ModelBundle, ModelBundleError
from .normalization import normalize_the_odds_api_offers
from .pipeline import RecommendationPipeline
from .recommendation import AdaptivePolicy
from .settings import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineResult:
    """Outcome of one engine cycle.

    ``errors`` are fatal (they degrade the engine); ``skips`` are nonfatal
    data-quality gaps that must not degrade the engine or abort other games.
    """

    recommendations: tuple[Recommendation, ...]
    games: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    skips: tuple[str, ...]

    def __iter__(self):
        # Backwards-compatible 3-tuple unpacking:
        #   recommendations, games, errors = engine.process(cycle)
        yield self.recommendations
        yield self.games
        yield self.errors


def summarize_live_games(
    cycle: CollectionCycle,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    summaries: list[dict[str, Any]] = []
    errors: list[str] = []

    rows = {
        int(row["id"]): row
        for row in cycle.bdl_games_payload.get("data", [])
    }

    for game_id, live_payload in cycle.live_game_payloads.items():
        row = rows.get(game_id)

        if row is None:
            errors.append(f"GAME_ROW_MISSING:{game_id}")
            continue

        plays = list(live_payload.plays.get("data", []))

        latest_play = max(
            plays,
            key=lambda item: int(item.get("order", 0) or 0),
            default=None,
        )

        try:
            game = normalize_bdl_game(
                row,
                received_at=cycle.captured_at,
                latest_play=latest_play,
            )
        except Exception as exc:
            errors.append(
                f"GAME_NORMALIZATION_FAILED:{game_id}:{exc}"
            )
            continue

        summaries.append(
            {
                "canonical_game_id": game.canonical_game_id,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "away_score": game.away_score,
                "home_score": game.home_score,
                "period": game.period,
                "clock_seconds": game.clock_seconds,
                "event_sequence": game.event_sequence,
                "state_age_seconds": game.age_seconds,
                "status": game.status,
            }
        )

    return tuple(summaries), tuple(errors)


def match_odds_event(
    bdl_game: Mapping[str, Any],
    odds_events: tuple[Mapping[str, Any], ...],
) -> Mapping[str, Any] | None:
    home = normalize_name(str(bdl_game["home_team"]["full_name"]))
    away_source = bdl_game.get("visitor_team", bdl_game.get("away_team", {}))
    away = normalize_name(str(away_source.get("full_name", "")))

    matches = [
        event
        for event in odds_events
        if normalize_name(str(event.get("home_team", ""))) == home
        and normalize_name(str(event.get("away_team", ""))) == away
    ]
    return matches[0] if len(matches) == 1 else None


class LiveRecommendationEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        identity: IdentityRegistry,
        model_bundle_path: Path,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.model_bundle_path = model_bundle_path
        self.pipeline = RecommendationPipeline(
            policy=AdaptivePolicy(
                hard_min_conservative_roi=settings.hard_min_conservative_roi,
                base_conservative_roi=settings.base_conservative_roi,
                min_book_count=settings.min_book_count,
                max_total_uncertainty=settings.max_total_uncertainty,
                min_calibration_score=settings.min_calibration_score,
                max_game_state_age_seconds=settings.max_game_state_age_seconds,
                max_market_age_seconds=settings.max_market_age_seconds,
                publication_ttl_seconds=settings.publication_ttl_seconds,
            )
        )

    def process(
        self,
        cycle: CollectionCycle,
    ) -> "EngineResult":
        # Fatal errors degrade the engine (status DEGRADED / fail-closed).
        # Nonfatal skips are expected data-quality gaps (unmatched game,
        # missing player profile, unsupported market) that must NOT degrade
        # the whole engine or abort other games.
        fatal_errors: list[str] = []
        skips: list[str] = []
        game_summaries, summary_errors = summarize_live_games(cycle)
        skips.extend(summary_errors)

        try:
            bundle = ModelBundle.load(self.model_bundle_path)
        except ModelBundleError as exc:
            # No usable production model is a genuine fatal condition.
            return EngineResult(
                recommendations=(),
                games=game_summaries,
                errors=(f"MODEL_BUNDLE_NOT_READY: {exc}",),
                skips=tuple(skips),
            )

        recommendations: list[Recommendation] = []

        bdl_rows = {
            int(row["id"]): row
            for row in cycle.bdl_games_payload.get("data", [])
        }
        for game_id, live_payload in cycle.live_game_payloads.items():
            row = bdl_rows.get(game_id)
            if row is None:
                skips.append(f"GAME_ROW_MISSING:{game_id}")
                continue

            plays = list(live_payload.plays.get("data", []))
            latest_play = max(
                plays,
                key=lambda item: int(item.get("order", 0) or 0),
                default=None,
            )
            game = normalize_bdl_game(
                row,
                received_at=cycle.captured_at,
                latest_play=latest_play,
            )
            odds_event = match_odds_event(row, cycle.odds_events_payload)
            if odds_event is None:
                skips.append(f"ODDS_EVENT_UNMATCHED:{game_id}")
                continue

            event_id = str(odds_event["id"])
            odds_payload = cycle.event_odds_payloads.get(event_id)
            if odds_payload is None:
                skips.append(f"EVENT_ODDS_MISSING:{event_id}")
                continue

            live_players = normalize_bdl_player_stats(
                live_payload.player_stats,
                identity=self.identity,
            )
            profiles: dict[str, PlayerRateProfile] = {}
            missing_profiles: list[str] = []
            for player_id, live in live_players.items():
                profile = bundle.profiles.get(player_id)
                if profile is None:
                    missing_profiles.append(player_id)
                    continue
                if profile.target_total_minutes is not None:
                    expected_remaining = max(
                        0.0,
                        min(
                            game.regulation_seconds_remaining / 60,
                            profile.target_total_minutes - live.minutes_played,
                        ),
                    )
                    profile = replace(
                        profile,
                        expected_remaining_minutes=expected_remaining,
                    )
                profiles[player_id] = profile

            # A missing profile can distort correlated totals, so we fail
            # closed for the affected game — but as a NONFATAL data-quality
            # skip that leaves the engine HEALTHY and every other game running.
            if missing_profiles:
                skips.append(
                    f"PLAYER_PROFILES_MISSING:{game_id}:"
                    + ",".join(missing_profiles)
                )
                logger.info(
                    "Skipping game %s: missing player profiles %s",
                    game_id,
                    missing_profiles,
                )
                continue

            offers = normalize_the_odds_api_offers(
                odds_payload,
                canonical_game_id=game.canonical_game_id,
                identity=self.identity,
                received_at=cycle.captured_at,
            )
            # Filter unsupported markets before pricing/simulation so an
            # ineligible market (e.g. player_assists) never reaches the
            # calibrator lookup.
            eligible = bundle.eligible_markets
            offers = tuple(
                offer for offer in offers if offer.market_key in eligible
            )
            try:
                game_recommendations = self.pipeline.evaluate_game(
                    game=game,
                    live_players=live_players,
                    profiles=profiles,
                    offers=offers,
                    model=bundle.metadata,
                    simulations=self.settings.default_simulations,
                    seed=self.settings.random_seed + game.event_sequence,
                )
            except Exception as exc:
                # A genuine simulation/pricing crash for one game is fatal
                # (surfaced), but must not abort the remaining games.
                fatal_errors.append(f"SIMULATION_FAILED:{game_id}:{exc}")
                logger.exception("Simulation failed for game %s", game_id)
                continue

            recommendations.extend(game_recommendations)

        return EngineResult(
            recommendations=tuple(recommendations),
            games=game_summaries,
            errors=tuple(fatal_errors),
            skips=tuple(skips),
        )
