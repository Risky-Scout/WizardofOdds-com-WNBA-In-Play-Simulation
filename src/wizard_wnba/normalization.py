from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from .domain import MarketOffer
from .identity import IdentityRegistry
from .odds_math import american_to_decimal


def parse_timestamp(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_the_odds_api_offers(
    payload: Mapping[str, Any] | list[Mapping[str, Any]],
    *,
    canonical_game_id: str,
    identity: IdentityRegistry,
    received_at: datetime,
) -> list[MarketOffer]:
    events = payload if isinstance(payload, list) else [payload]
    output: list[MarketOffer] = []

    for event in events:
        event_id = str(event.get("id", ""))
        for bookmaker in event.get("bookmakers", []):
            bookmaker_key = str(bookmaker.get("key", ""))
            bookmaker_title = str(bookmaker.get("title", bookmaker_key))
            bookmaker_update = parse_timestamp(
                bookmaker.get("last_update"),
                received_at,
            )
            for market in bookmaker.get("markets", []):
                market_key = str(market.get("key", ""))
                market_update = parse_timestamp(
                    market.get("last_update"),
                    bookmaker_update,
                )
                for outcome in market.get("outcomes", []):
                    name = str(outcome.get("name", ""))
                    description = outcome.get("description")
                    player_name = str(description) if description else None
                    side = name.lower()
                    point = (
                        float(outcome["point"])
                        if outcome.get("point") is not None
                        else None
                    )
                    home_team = str(event.get("home_team", ""))
                    away_team = str(event.get("away_team", ""))
                    if market_key == "h2h":
                        if name == home_team:
                            side, point = "over", 0.5
                        elif name == away_team:
                            side, point = "under", 0.5
                    elif market_key == "spreads":
                        if name == home_team and point is not None:
                            side, point = "over", -point
                        elif name == away_team and point is not None:
                            side, point = "under", point
                    elif side not in {"over", "under"}:
                        side = name.lower()
                    crosswalk = (
                        identity.resolve_name(player_name)
                        if player_name
                        else None
                    )
                    american = int(outcome["price"])
                    output.append(
                        MarketOffer(
                            provider="the_odds_api",
                            bookmaker_key=bookmaker_key,
                            bookmaker_title=bookmaker_title,
                            provider_event_id=event_id,
                            canonical_game_id=canonical_game_id,
                            market_key=market_key,
                            outcome_name=name,
                            side=side.lower(),
                            line=point,
                            american_odds=american,
                            decimal_odds=american_to_decimal(american),
                            player_name=player_name,
                            canonical_player_id=(
                                crosswalk.canonical_player_id
                                if crosswalk
                                else None
                            ),
                            last_update=market_update,
                            received_timestamp=received_at,
                            deep_link=outcome.get("link")
                            or bookmaker.get("link"),
                        )
                    )
    return output


def normalize_balldontlie_props(
    payload: Mapping[str, Any],
    *,
    canonical_game_id: str,
    identity: IdentityRegistry,
    received_at: datetime,
) -> list[MarketOffer]:
    rows = payload.get("data", payload.get("player_props", []))
    output: list[MarketOffer] = []

    for row in rows:
        player = row.get("player", {})
        player_name = (
            f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            or row.get("player_name")
        )
        crosswalk = identity.resolve_name(str(player_name)) if player_name else None
        vendor = str(row.get("vendor", row.get("sportsbook", "unknown")))
        market_key = str(row.get("prop_type", row.get("market", "unknown")))
        line = row.get("line_value", row.get("line"))

        for side_name, price_keys in {
            "over": ("over_odds", "over_price"),
            "under": ("under_odds", "under_price"),
        }.items():
            price = next(
                (row.get(key) for key in price_keys if row.get(key) is not None),
                None,
            )
            if price is None:
                continue
            american = int(price)
            output.append(
                MarketOffer(
                    provider="balldontlie",
                    bookmaker_key=vendor,
                    bookmaker_title=vendor.replace("_", " ").title(),
                    provider_event_id=str(row.get("game_id", "")),
                    canonical_game_id=canonical_game_id,
                    market_key=market_key,
                    outcome_name=side_name.title(),
                    side=side_name,
                    line=float(line) if line is not None else None,
                    american_odds=american,
                    decimal_odds=american_to_decimal(american),
                    player_name=str(player_name) if player_name else None,
                    canonical_player_id=(
                        crosswalk.canonical_player_id if crosswalk else None
                    ),
                    last_update=parse_timestamp(
                        row.get("updated_at"),
                        received_at,
                    ),
                    received_timestamp=received_at,
                )
            )
    return output
