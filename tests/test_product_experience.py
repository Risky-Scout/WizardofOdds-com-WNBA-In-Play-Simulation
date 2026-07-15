from __future__ import annotations

from fastapi.testclient import TestClient

from wizard_wnba.api import app
from wizard_wnba.demo import build_demo_snapshot
from wizard_wnba.product import BOOKMAKERS, PUBLIC_ODDS_FORMAT


def test_public_product_is_american_odds_only_and_includes_bovada():
    snapshot = build_demo_snapshot(simulations=300, seed=17).to_dict()
    assert PUBLIC_ODDS_FORMAT == "american"
    assert any(book["key"] == "bovada" for book in BOOKMAKERS)
    assert any(
        recommendation["bookmaker_key"] == "bovada"
        for recommendation in snapshot["recommendations"]
    )

    for recommendation in snapshot["recommendations"]:
        assert recommendation["odds_format"] == "american"
        assert "american_odds" in recommendation
        assert "fair_american_odds" in recommendation
        assert "decimal_odds" not in recommendation
        assert "fair_decimal_odds" not in recommendation


def test_public_route_preferences_and_scenario_lab():
    with TestClient(app) as client:
        page = client.get(
            "/tools/odds-scanner/predictions/WNBA/In-Play/Simulation/"
        )
        assert page.status_code == 200
        assert "Scenario Lab" in page.text
        assert "My WNBA preferences" in page.text
        assert "AMERICAN" in page.text

        options = client.get("/api/v1/product-options")
        assert options.status_code == 200
        payload = options.json()
        assert payload["odds_format"] == "american"
        assert payload["minimum_conservative_roi"] == 0.02
        assert any(
            book["key"] == "bovada"
            for book in payload["bookmakers"]
        )

        snapshot = client.get("/api/v1/snapshot").json()
        recommendation = next(
            item
            for item in snapshot["recommendations"]
            if item["market_key"] != "h2h"
        )
        scenario = client.post(
            "/api/v1/scenario-lab",
            json={
                "recommendation_id": recommendation["recommendation_id"],
                "side": recommendation["side"],
                "line": recommendation["line"],
                "american_odds": recommendation["american_odds"],
                "uncertainty_multiplier": 1.0,
            },
        )
        assert scenario.status_code == 200
        result = scenario.json()
        assert result["official_recommendation"] is False
        assert result["odds_format"] == "american"
        assert result["american_odds"] == recommendation["american_odds"]
        assert "fair_american_odds" in result
        assert result["distribution_source"] == "exact_simulation_pmf"
        assert abs(
            result["p_win"] + result["p_push"] + result["p_loss"] - 1.0
        ) < 1e-9


def test_recommendation_api_filters_books_and_roi():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/recommendations",
            params=[
                ("bookmaker", "bovada"),
                ("min_conservative_roi", "0.02"),
                ("min_grade", "WATCH"),
            ],
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["odds_format"] == "american"
        assert all(
            item["bookmaker_key"] == "bovada"
            for item in payload["recommendations"]
        )
        assert all(
            item["conservative_roi"] >= 0.02
            for item in payload["recommendations"]
        )


def test_scenario_requires_valid_american_odds():
    with TestClient(app) as client:
        snapshot = client.get("/api/v1/snapshot").json()
        recommendation = next(
            item
            for item in snapshot["recommendations"]
            if item["market_key"] != "h2h"
        )
        response = client.post(
            "/api/v1/scenario-lab",
            json={
                "recommendation_id": recommendation["recommendation_id"],
                "side": recommendation["side"],
                "line": recommendation["line"],
                "american_odds": 50,
            },
        )
        assert response.status_code == 422
