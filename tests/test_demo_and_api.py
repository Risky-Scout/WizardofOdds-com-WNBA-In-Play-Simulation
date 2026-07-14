from __future__ import annotations

from fastapi.testclient import TestClient

from wizard_wnba.api import app
from wizard_wnba.demo import build_demo_snapshot


def test_demo_snapshot_has_current_metadata_and_no_legacy_clv_fields():
    snapshot = build_demo_snapshot(simulations=300, seed=3).to_dict()
    rendered = str(snapshot)
    assert snapshot["metrics"]["hard_min_conservative_roi"] == .02
    assert "clv_adj_edge" not in rendered
    assert "clv_proxy" not in rendered


def test_api_health_and_dashboard():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Best available opportunities" in dashboard.text
        model_card = client.get("/api/v1/model-card")
        assert model_card.json()["policy"]["hard_min_conservative_roi"] == .02
