from __future__ import annotations

from datetime import UTC, datetime

from wizard_wnba.identity import IdentityRegistry, PlayerCrosswalk
from wizard_wnba.normalization import normalize_the_odds_api_offers


def test_player_prop_and_spread_normalization(tmp_path):
    registry = IdentityRegistry(tmp_path / "crosswalk.json")
    registry.upsert(
        PlayerCrosswalk(
            canonical_player_id="p1",
            display_name="A'ja Wilson",
            team="Las Vegas Aces",
            odds_api_name="A'ja Wilson",
        )
    )
    payload = {
        "id": "event",
        "home_team": "Las Vegas Aces",
        "away_team": "New York Liberty",
        "bookmakers": [
            {
                "key": "book",
                "title": "Book",
                "last_update": "2026-07-14T20:00:00Z",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {"name": "Over", "description": "A'ja Wilson", "price": 105, "point": 25.5},
                            {"name": "Under", "description": "A'ja Wilson", "price": -125, "point": 25.5}
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Las Vegas Aces", "price": -110, "point": -4.5},
                            {"name": "New York Liberty", "price": -110, "point": 4.5}
                        ],
                    }
                ],
            }
        ],
    }
    offers = normalize_the_odds_api_offers(
        payload,
        canonical_game_id="g",
        identity=registry,
        received_at=datetime.now(UTC),
    )
    player_over = next(
        item for item in offers
        if item.market_key == "player_points" and item.side == "over"
    )
    home_spread = next(
        item for item in offers
        if item.market_key == "spreads" and item.side == "over"
    )
    away_spread = next(
        item for item in offers
        if item.market_key == "spreads" and item.side == "under"
    )
    assert player_over.canonical_player_id == "p1"
    assert home_spread.line == 4.5
    assert away_spread.line == 4.5
