from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from wizard_wnba.collector import CollectionCycle, LiveGamePayload
from wizard_wnba.identity import IdentityRegistry
from wizard_wnba.live_engine import EngineResult, LiveRecommendationEngine
from wizard_wnba.model_bundle import ModelBundle, ModelBundleError
from wizard_wnba.settings import Settings
from support import production_model_dict, write_production_model


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        DATA_DIR=str(tmp_path),
        ENVIRONMENT="test",
        DEFAULT_SIMULATIONS=1000,
    )


def _engine(tmp_path: Path, model_path: Path) -> LiveRecommendationEngine:
    settings = _settings(tmp_path)
    return LiveRecommendationEngine(
        settings,
        identity=IdentityRegistry(
            tmp_path / "models" / "player_crosswalk.json"
        ),
        model_bundle_path=model_path,
    )


def _empty_cycle() -> CollectionCycle:
    return CollectionCycle(
        captured_at=datetime.now(UTC),
        bdl_games_payload={"data": []},
        odds_events_payload=(),
        featured_odds_payload=(),
        event_odds_payloads={},
        live_game_payloads={},
        quota_remaining=100,
        errors=(),
    )


def _cycle_with_missing_profile_game() -> CollectionCycle:
    game_id = 24930
    bdl_row = {
        "id": game_id,
        "home_team": {"full_name": "Toronto Tempo"},
        "visitor_team": {"full_name": "Washington Mystics"},
        "status": "in",
        "period": 2,
        "home_score": 30,
        "away_score": 19,
    }
    # Players whose ids are NOT in the production bundle (which has 101-114).
    player_stats = {
        "data": [
            {
                "player": {"id": 9999, "first_name": "Ghost", "last_name": "One"},
                "team": {"full_name": "Washington Mystics"},
                "min": "10:00",
                "pts": 8,
                "reb": 3,
                "ast": 2,
                "fg3m": 1,
            }
        ]
    }
    plays = {
        "data": [
            {
                "order": 5,
                "period": 2,
                "home_score": 30,
                "away_score": 19,
                "clock": "3:02",
            }
        ]
    }
    payload = LiveGamePayload(
        game_id=game_id,
        plays=plays,
        player_stats=player_stats,
        player_props={"data": []},
    )
    return CollectionCycle(
        captured_at=datetime.now(UTC),
        bdl_games_payload={"data": [bdl_row]},
        odds_events_payload=(
            {
                "id": "evt-1",
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
            },
        ),
        featured_odds_payload=(),
        event_odds_payloads={"evt-1": {"bookmakers": []}},
        live_game_payloads={game_id: payload},
        quota_remaining=100,
        errors=(),
    )


# --- Engine behavior --------------------------------------------------------


def test_engine_missing_model_bundle_is_fatal(tmp_path):
    engine = _engine(tmp_path, tmp_path / "models" / "does-not-exist.json")
    result = engine.process(_empty_cycle())
    assert isinstance(result, EngineResult)
    assert any("MODEL_BUNDLE_NOT_READY" in e for e in result.errors)
    assert result.recommendations == ()


def test_engine_empty_cycle_is_healthy(tmp_path):
    model_path = write_production_model(tmp_path)
    engine = _engine(tmp_path, model_path)
    result = engine.process(_empty_cycle())
    # No live games -> no recommendations, but NO fatal errors and NO skips.
    assert result.errors == ()
    assert result.skips == ()
    assert result.recommendations == ()


def test_engine_isolates_missing_profiles_as_nonfatal_skip(tmp_path):
    model_path = write_production_model(tmp_path)
    engine = _engine(tmp_path, model_path)
    result = engine.process(_cycle_with_missing_profile_game())

    # Missing profile is a nonfatal skip, never a fatal engine error.
    assert result.errors == ()
    assert any(s.startswith("PLAYER_PROFILES_MISSING") for s in result.skips)
    assert result.recommendations == ()


def test_engine_result_supports_three_tuple_unpacking(tmp_path):
    model_path = write_production_model(tmp_path)
    engine = _engine(tmp_path, model_path)
    recommendations, games, errors = engine.process(_empty_cycle())
    assert recommendations == ()
    assert errors == ()


# --- Model bundle contract --------------------------------------------------


def test_model_bundle_exposes_full_contract(tmp_path):
    model_path = write_production_model(tmp_path)
    bundle = ModelBundle.load(model_path)
    assert bundle.probability_source == "possession_raw_probability"
    assert bundle.model_version == "wnba-test-walkforward-20260715"
    assert len(bundle.model_hash) == 64  # sha256 hex
    assert bundle.eligible_markets == {
        "h2h",
        "player_points",
        "player_rebounds",
        "player_threes",
        "spreads",
        "totals",
    }
    assert "player_assists" not in bundle.eligible_markets
    assert "player_points_rebounds_assists" not in bundle.eligible_markets


def test_model_bundle_rejects_wrong_probability_source(tmp_path):
    data = production_model_dict()
    data["validation_report"]["probability_source"] = "legacy_monte_carlo"
    path = tmp_path / "models" / "production.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ModelBundleError):
        ModelBundle.load(path)


def test_model_bundle_rejects_forbidden_market(tmp_path):
    data = production_model_dict()
    data["metadata"]["calibration_parameters"]["player_assists"] = [1.0, -1.0, 0.0]
    path = tmp_path / "models" / "production.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ModelBundleError):
        ModelBundle.load(path)
