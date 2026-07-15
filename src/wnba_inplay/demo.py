from __future__ import annotations

from dataclasses import asdict
import time

from .contracts import MarketSpec, RunMetadata
from .domain import GameConfig
from .events import GameEvent
from .fingerprint import fingerprint
from .pricing import CalibrationStatus, QuoteContext
from .rotation import PlayerRotationProfile
from .service import SimulationRequest
from .simulation import PlayerEventProfile, TeamSimulationProfile
from .state_engine import StateEngine


def build_demo_request(
    simulations: int = 250,
    seed: int = 42,
) -> SimulationRequest:
    config = GameConfig(
        game_id="demo-game",
        home_team="HOME",
        away_team="AWAY",
    )
    players = [
        {
            "player_id": f"{team}-{number}",
            "team_id": team,
            "active": True,
        }
        for team in ("HOME", "AWAY")
        for number in range(1, 8)
    ]
    now_ms = int(time.time() * 1000)
    start = GameEvent(
        event_id="demo-start",
        game_id=config.game_id,
        sequence=1,
        event_type="game_started",
        source_timestamp_ms=now_ms - 100,
        received_timestamp_ms=now_ms - 90,
        payload={
            "players": players,
            "home_lineup": [f"HOME-{i}" for i in range(1, 6)],
            "away_lineup": [f"AWAY-{i}" for i in range(1, 6)],
            "possession_team": "HOME",
        },
    )
    engine = StateEngine(config)
    state = engine.apply(start)
    state.period = 4
    state.clock_seconds = 120
    state.home_score = 72
    state.away_score = 71
    state.players["HOME-1"].stats.points = 18
    state.players["HOME-1"].stats.rebounds = 5
    state.players["HOME-1"].stats.assists = 4

    rotations = {}
    events = {}
    for player in state.players.values():
        number = int(player.player_id.split("-")[-1])
        rotations[player.player_id] = PlayerRotationProfile(
            player_id=player.player_id,
            target_total_minutes=32 if number <= 5 else 12,
            closing_priority=1.0 if number <= 5 else 0.0,
        )
        events[player.player_id] = PlayerEventProfile(
            player_id=player.player_id,
            usage_weight=1.6 if number <= 2 else 1.0,
            three_point_share=0.34,
            two_point_pct=0.50,
            three_point_pct=0.35,
            free_throw_pct=0.80,
            turnover_probability=0.11,
            shooting_foul_probability=0.12,
            assist_weight=1.5 if number == 1 else 1.0,
            offensive_rebound_weight=1.5 if number >= 4 else 0.8,
            defensive_rebound_weight=1.5 if number >= 4 else 0.8,
            steal_probability=0.03,
            block_probability=0.02 if number >= 4 else 0.007,
        )

    teams = {
        "HOME": TeamSimulationProfile("HOME", pace_per_40=79),
        "AWAY": TeamSimulationProfile("AWAY", pace_per_40=78),
    }
    markets = (
        MarketSpec(
            market_id="home-1-points-20",
            market_type="player_prop",
            player_id="HOME-1",
            stats=("points",),
            line=20,
        ),
        MarketSpec(
            market_id="home-1-pra-29.5",
            market_type="player_prop",
            player_id="HOME-1",
            stats=("points", "rebounds", "assists"),
            line=29.5,
        ),
        MarketSpec(
            market_id="game-total-149.5",
            market_type="game_total",
            line=149.5,
        ),
        MarketSpec(
            market_id="home-spread-1.5",
            market_type="home_spread",
            line=1.5,
        ),
    )
    quote_contexts = {
        market.market_id: QuoteContext(
            base_margin=0.04,
            base_limit=1000,
            exposure_over=0,
            exposure_under=0,
            risk_capacity=10000,
            input_age_ms=100,
            max_input_age_ms=2000,
            model_uncertainty=0.02,
            max_model_uncertainty=0.10,
            monte_carlo_error=0.005,
            max_monte_carlo_error=0.03,
            calibration=CalibrationStatus(True, "demo-cal-v1"),
        )
        for market in markets
    }
    metadata = RunMetadata(
        run_id=f"demo-{now_ms}",
        commit_sha="local-reference-build",
        model_version="demo-model-v1",
        calibration_version="demo-cal-v1",
        event_sequence=state.sequence,
        as_of_timestamp_ms=now_ms,
        source_timestamp_ms=state.last_source_timestamp_ms,
        input_fingerprint=fingerprint(state.to_dict()),
    )
    return SimulationRequest(
        state=state,
        rotation_profiles=rotations,
        player_profiles=events,
        team_profiles=teams,
        markets=markets,
        quote_contexts=quote_contexts,
        metadata=metadata,
        simulations=simulations,
        seed=seed,
    )
