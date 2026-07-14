from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .domain import GameState, MarketOffer, PlayerLiveState, PlayerRateProfile
from .odds_math import american_to_decimal
from .pipeline import ModelMetadata, RecommendationPipeline
from .publication import recommendation_snapshot


def build_demo_snapshot(simulations: int = 4000, seed: int = 42):
    now = datetime.now(UTC)
    game = GameState(
        canonical_game_id="wnba-demo-001",
        source_game_id="24752",
        home_team="Las Vegas Aces",
        away_team="New York Liberty",
        period=3,
        clock_seconds=252,
        home_score=61,
        away_score=59,
        possession_team="Las Vegas Aces",
        event_sequence=184,
        source_timestamp=now - timedelta(milliseconds=900),
        received_timestamp=now - timedelta(milliseconds=450),
        home_lineup=("lv-1", "lv-2", "lv-3", "lv-4", "lv-5"),
        away_lineup=("ny-1", "ny-2", "ny-3", "ny-4", "ny-5"),
    )

    live_players = {
        "lv-1": PlayerLiveState(
            canonical_player_id="lv-1",
            display_name="A'ja Wilson",
            team="Las Vegas Aces",
            on_court=True,
            active=True,
            minutes_played=24.6,
            current_stint_seconds=230,
            fouls=2,
            injury_state="healthy",
            points=19,
            rebounds=8,
            assists=2,
            threes=0,
        ),
        "lv-2": PlayerLiveState(
            "lv-2", "Jackie Young", "Las Vegas Aces", True, True,
            25.1, 230, 2, "healthy", 13, 3, 5, 2
        ),
        "lv-3": PlayerLiveState(
            "lv-3", "Chelsea Gray", "Las Vegas Aces", True, True,
            22.0, 150, 1, "healthy", 8, 2, 6, 1
        ),
        "ny-1": PlayerLiveState(
            "ny-1", "Breanna Stewart", "New York Liberty", True, True,
            25.4, 210, 3, "healthy", 17, 7, 3, 1
        ),
        "ny-2": PlayerLiveState(
            "ny-2", "Sabrina Ionescu", "New York Liberty", True, True,
            24.8, 210, 2, "healthy", 15, 4, 5, 3
        ),
        "ny-3": PlayerLiveState(
            "ny-3", "Jonquel Jones", "New York Liberty", True, True,
            21.7, 120, 4, "healthy", 10, 8, 2, 0
        ),
    }
    profiles = {
        "lv-1": PlayerRateProfile("lv-1", 10.5, 1.6, .78, .35, .10, .02, 1.12),
        "lv-2": PlayerRateProfile("lv-2", 9.2, 1.8, .56, .15, .22, .09, 1.04),
        "lv-3": PlayerRateProfile("lv-3", 8.8, 2.0, .40, .11, .29, .06, .96),
        "ny-1": PlayerRateProfile("ny-1", 10.1, 1.7, .68, .29, .13, .05, 1.08),
        "ny-2": PlayerRateProfile("ny-2", 9.7, 1.6, .62, .14, .25, .12, 1.10),
        "ny-3": PlayerRateProfile("ny-3", 7.4, 2.6, .55, .39, .11, .02, .98),
    }

    offer_specs = [
        ("draftkings", "DraftKings", "player_points", "lv-1", "A'ja Wilson", 26.5, -105, -115),
        ("fanduel", "FanDuel", "player_points", "lv-1", "A'ja Wilson", 26.5, 105, -135),
        ("caesars", "Caesars", "player_points", "lv-1", "A'ja Wilson", 26.5, 100, -130),
        ("draftkings", "DraftKings", "player_rebounds", "ny-1", "Breanna Stewart", 10.5, 110, -140),
        ("fanduel", "FanDuel", "player_rebounds", "ny-1", "Breanna Stewart", 10.5, 115, -145),
        ("caesars", "Caesars", "player_rebounds", "ny-1", "Breanna Stewart", 10.5, 105, -135),
        ("draftkings", "DraftKings", "player_assists", "ny-2", "Sabrina Ionescu", 7.5, 100, -130),
        ("fanduel", "FanDuel", "player_assists", "ny-2", "Sabrina Ionescu", 7.5, 105, -135),
        ("caesars", "Caesars", "player_assists", "ny-2", "Sabrina Ionescu", 7.5, -105, -125),
        ("draftkings", "DraftKings", "totals", None, None, 157.5, -108, -112),
        ("fanduel", "FanDuel", "totals", None, None, 157.5, -105, -115),
        ("caesars", "Caesars", "totals", None, None, 157.5, -110, -110),
    ]
    offers: list[MarketOffer] = []
    for index, (
        book_key, book_title, market, player_id, player_name, line, over, under
    ) in enumerate(offer_specs):
        for side, american in (("over", over), ("under", under)):
            offers.append(
                MarketOffer(
                    provider="demo",
                    bookmaker_key=book_key,
                    bookmaker_title=book_title,
                    provider_event_id="demo-event",
                    canonical_game_id=game.canonical_game_id,
                    market_key=market,
                    outcome_name=side.title(),
                    side=side,
                    line=line,
                    american_odds=american,
                    decimal_odds=american_to_decimal(american),
                    player_name=player_name,
                    canonical_player_id=player_id,
                    last_update=now - timedelta(seconds=1 + index * 0.08),
                    received_timestamp=now - timedelta(seconds=.5 + index * 0.05),
                    deep_link="https://wizardofodds.com/",
                )
            )

    model = ModelMetadata(
        model_version="demo-reference-0.1.0",
        calibrator_id="demo-calibrator-2026-01",
        calibration_score=0.91,
        calibration_se=0.008,
        model_se=0.018,
        calibration_parameters={
            "player_points": (1.0, -1.0, 0.0),
            "player_rebounds": (1.0, -1.0, 0.0),
            "player_assists": (1.0, -1.0, 0.0),
            "totals": (1.0, -1.0, 0.0),
        },
    )
    recommendations = RecommendationPipeline().evaluate_game(
        game=game,
        live_players=live_players,
        profiles=profiles,
        offers=offers,
        model=model,
        simulations=simulations,
        seed=seed,
    )
    game_payload = {
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
    return recommendation_snapshot(
        recommendations,
        environment="demo",
        engine_status="HEALTHY",
        data_status="DEMO_DATA",
        games=(game_payload,),
        extra_metrics={
            "model_version": model.model_version,
            "calibrator_id": model.calibrator_id,
            "simulation_count": simulations,
            "provider_status": {
                "balldontlie": "KEY_NOT_CONFIGURED",
                "the_odds_api": "KEY_NOT_CONFIGURED",
                "sportsdataverse": "OPTIONAL",
            },
        },
    )
