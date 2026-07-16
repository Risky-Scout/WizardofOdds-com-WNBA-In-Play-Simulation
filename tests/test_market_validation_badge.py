from __future__ import annotations

from pathlib import Path

from wizard_wnba.model_bundle import ModelBundle, load_per_market_validation
from support import (
    six_book_feed,
    snapshot_with_game,
    write_player_rows,
    write_production_model,
)


def test_bundle_exposes_per_market_validation(tmp_path):
    path = write_production_model(tmp_path)
    bundle = ModelBundle.load(path)
    v = bundle.per_market_validation
    assert v["h2h"] is True
    assert v["spreads"] is False
    assert v["player_points"] is False


def test_missing_gate_is_all_not_validated(tmp_path):
    # A bundle without per_market_gate must not validate any market.
    import json
    from support import production_model_dict
    data = production_model_dict()
    data["validation_report"].pop("per_market_gate", None)
    (tmp_path / "models").mkdir(parents=True)
    p = tmp_path / "models" / "production.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert ModelBundle.load(p).per_market_validation == {}


def test_load_per_market_validation_safe_default(tmp_path):
    # No bundle present -> empty (nothing validated), never raises.
    assert load_per_market_validation(tmp_path) == {}


def test_load_per_market_validation_reads_bundle(tmp_path):
    write_production_model(tmp_path)
    v = load_per_market_validation(tmp_path)
    assert v.get("h2h") is True and v.get("totals") is False


def test_live_reference_result_carries_oos_validated(tmp_path, monkeypatch):
    # End-to-end through the API: validated market -> oos_validated True.
    import wizard_wnba.api as api
    from fastapi.testclient import TestClient

    write_production_model(api.settings.data_dir)
    write_player_rows(api.settings.data_dir)
    feed = six_book_feed("h2h")
    snap = snapshot_with_game()

    monkeypatch.setattr(api, "build_live_market_feed", lambda *a, **k: feed)
    monkeypatch.setattr(
        api.publication_store, "read", lambda: {**snap, "recommendations": []}
    )

    with TestClient(api.app) as client:
        r = client.post("/api/v1/live-reference-simulation",
                        json={"market_id": "draftkings-h2h", "simulations": 2000})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["market_key"] == "h2h"
        assert body["oos_validated"] is True

    # live-markets feed is annotated too
    with TestClient(api.app) as client:
        feed_resp = client.get("/api/v1/live-markets").json()
        h2h_rows = [m for m in feed_resp["markets"] if m["market_key"] == "h2h"]
        assert h2h_rows and all(m["oos_validated"] is True for m in h2h_rows)
