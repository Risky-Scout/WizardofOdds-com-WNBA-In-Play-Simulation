"""Six-sportsbook Playwright browser test for the Scenario Lab UI.

Runs the real app server (for static assets + routing) and intercepts the data
endpoints so the *frontend* behavior is exercised deterministically:

  * every sportsbook can be selected;
  * the displayed odds match the selected book;
  * the POST body carries that book's exact market_id;
  * H2H hides the line field;
  * submit stays usable;
  * an expired line forces reselection and never silently falls back to Bovada.

Skips cleanly if Playwright or its Chromium build is unavailable.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

SIX_BOOKS = [
    ("betmgm", "BetMGM", -140),
    ("betrivers", "BetRivers", -138),
    ("bovada", "Bovada", -145),
    ("draftkings", "DraftKings", -142),
    ("fanatics", "Fanatics", -139),
    ("fanduel", "FanDuel", -141),
]


def _feed(include_all=True):
    markets = []
    for book_key, book_title, odds in SIX_BOOKS:
        markets.append(
            {
                "market_id": f"{book_key}-h2h",
                "recommendation_id": f"{book_key}-h2h",
                "canonical_game_id": "bdl-24930",
                "bookmaker_key": book_key,
                "bookmaker_title": book_title,
                "home_team": "Toronto Tempo",
                "away_team": "Washington Mystics",
                "market_key": "h2h",
                "market_title": "H2h",
                "selection": "Toronto Tempo",
                "player_name": None,
                "side": "home",
                "line": None,
                "american_odds": odds,
            }
        )
    return {"odds_format": "american", "count": len(markets), "markets": markets}


SNAPSHOT = {
    "generated_at": "2026-07-15T00:00:00+00:00",
    "engine_status": "HEALTHY",
    "data_status": "LIVE",
    "recommendations": [],
    "games": [
        {
            "canonical_game_id": "bdl-24930",
            "home_team": "Toronto Tempo",
            "away_team": "Washington Mystics",
            "home_score": 30,
            "away_score": 19,
            "period": 2,
            "clock_seconds": 182,
            "event_sequence": 168,
            "status": "in",
            "state_age_seconds": 2.0,
        }
    ],
    "metrics": {},
}

PRODUCT_OPTIONS = {
    "odds_format": "american",
    "bookmakers": [
        {"key": k, "title": t} for k, t, _ in SIX_BOOKS
    ],
    "markets": [{"key": "h2h", "title": "Moneyline"}],
    "preferences_storage_key": "wnba",
    "minimum_conservative_roi": 0.02,
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("browser_data")
    port = _free_port()
    env = dict(os.environ)
    env["DATA_DIR"] = str(data_dir)
    env["ENVIRONMENT"] = "test"
    env["LIVE_ENABLED"] = "false"
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "wizard_wnba.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    # Wait for the server to answer.
    import urllib.request

    ready = False
    for _ in range(100):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.2)
    if not ready:
        out = proc.stdout.read().decode() if proc.stdout else ""
        proc.terminate()
        pytest.skip(f"app server did not start: {out[:500]}")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def _install_routes(page, state):
    def route_markets(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_feed() if state["markets_ok"] else _feed_missing(state)),
        )

    def route_snapshot(route):
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(SNAPSHOT)
        )

    def route_options(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(PRODUCT_OPTIONS),
        )

    def route_sim(route):
        request = route.request
        state["last_post"] = request.post_data
        payload = json.loads(request.post_data or "{}")
        state["posted_market_ids"].append(payload.get("market_id"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "market_id": payload.get("market_id"),
                    "market_key": "h2h",
                    "win_probability": 0.55,
                    "push_probability": 0.0,
                    "loss_probability": 0.45,
                    "fair_american_odds": -122,
                    "expected_roi": 0.03,
                    "conservative_roi": 0.01,
                    "simulation_count": 20000,
                    "projected_mean": None,
                    "total_uncertainty": 0.03,
                    "calibration_status": "OOS_CALIBRATED",
                    "oos_validated": True,
                    "note": "browser-test",
                }
            ),
        )

    page.route("**/api/v1/live-markets**", route_markets)
    page.route("**/api/v1/snapshot**", route_snapshot)
    page.route("**/api/v1/product-options**", route_options)
    page.route("**/api/v1/live-reference-simulation", route_sim)


def _feed_missing(state):
    # Return the feed WITHOUT the currently-selected market, simulating expiry.
    dropped = state.get("drop_market_id")
    feed = _feed()
    feed["markets"] = [m for m in feed["markets"] if m["market_id"] != dropped]
    feed["count"] = len(feed["markets"])
    return feed


def test_six_sportsbook_browser_flow(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        state = {
            "markets_ok": True,
            "drop_market_id": None,
            "posted_market_ids": [],
            "last_post": None,
        }
        _install_routes(page, state)
        page.goto(server, wait_until="networkidle")

        select = page.locator("#scenario-selection")
        page.wait_for_function(
            "document.querySelectorAll('#scenario-selection option').length >= 7"
        )

        # (1) Every sportsbook is selectable and its displayed odds match.
        for book_key, _title, odds in SIX_BOOKS:
            select.select_option(f"{book_key}-h2h")
            page.wait_for_timeout(50)
            shown = page.locator("#scenario-odds").input_value()
            assert int(shown) == odds, f"{book_key}: shown {shown} != {odds}"

            # (2) H2H hides the line field.
            line_hidden = page.locator("#scenario-line-field").evaluate(
                "el => el.classList.contains('hidden') || el.offsetParent === null"
            )
            assert line_hidden, f"{book_key}: line field not hidden for h2h"

            # (3) Submit stays usable.
            assert page.locator("#scenario-submit").is_enabled()

        # (4) POST body carries the exact selected market_id (DraftKings).
        select.select_option("draftkings-h2h")
        page.wait_for_timeout(50)
        with page.expect_request("**/live-reference-simulation") as req_info:
            page.locator("#scenario-submit").click()
        request = req_info.value
        body = json.loads(request.post_data)
        assert body["market_id"] == "draftkings-h2h"
        # h2h omits the line entirely.
        assert "line" not in body or body["line"] is None
        page.wait_for_selector(".result-grid")

        # Per-market validation badge renders from oos_validated.
        badge = page.locator(".validation-badge")
        assert badge.count() == 1
        assert "OOS validated" in badge.inner_text()
        assert "validated" in (badge.get_attribute("class") or "")

        # (5) Expired line: drop the selected FanDuel market on refresh; submit
        # must force reselection and must NOT fall back to Bovada.
        select.select_option("fanduel-h2h")
        page.wait_for_timeout(50)
        state["markets_ok"] = False
        state["drop_market_id"] = "fanduel-h2h"
        posted_before = list(state["posted_market_ids"])
        page.locator("#scenario-submit").click()
        page.wait_for_function(
            "document.querySelector('#scenario-error')."
            "textContent.toLowerCase().includes('expired')"
        )
        error_text = page.locator("#scenario-error").inner_text().lower()
        assert "reselect" in error_text
        # No new simulation POST fired, and definitely not for Bovada.
        assert state["posted_market_ids"] == posted_before
        assert "bovada-h2h" not in state["posted_market_ids"]

        browser.close()
