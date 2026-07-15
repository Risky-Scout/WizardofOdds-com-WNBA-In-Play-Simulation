
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


def _team_key(value: object) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return words[-1] if words else ""


def _events(payload: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                yield item
    elif isinstance(payload, Mapping):
        yield payload


def _outcome_count(event: Mapping[str, Any]) -> int:
    return sum(
        len(market.get("outcomes", []))
        for book in event.get("bookmakers", [])
        for market in book.get("markets", [])
    )


def build_live_market_feed(
    data_dir: Path,
    active_games: list[dict[str, Any]],
) -> dict[str, Any]:
    game_pairs: dict[tuple[str, str], dict[str, Any]] = {}

    for game in active_games:
        pair = (
            _team_key(game.get("away_team")),
            _team_key(game.get("home_team")),
        )
        if all(pair):
            game_pairs[pair] = game

    root = data_dir / "raw" / "provider=the_odds_api"

    if not root.exists() or not game_pairs:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "odds_format": "american",
            "count": 0,
            "markets": [],
        }

    files = sorted(
        root.rglob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    candidates: dict[
        str,
        tuple[tuple[int, int, float], Mapping[str, Any], dict[str, Any], Path],
    ] = {}

    for path in files[:800]:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        payload = (
            envelope.get("payload")
            if isinstance(envelope, Mapping)
            else envelope
        )

        for event in _events(payload):
            books = event.get("bookmakers", [])
            if not isinstance(books, list) or not books:
                continue

            pair = (
                _team_key(event.get("away_team")),
                _team_key(event.get("home_team")),
            )
            game = game_pairs.get(pair)

            if game is None:
                continue

            event_id = str(
                event.get("id")
                or event.get("event_id")
                or f"{pair[0]}-{pair[1]}"
            )

            modified = path.stat().st_mtime
            rank = (
                int(modified // 30),
                _outcome_count(event),
                modified,
            )

            previous = candidates.get(event_id)

            if previous is None or rank > previous[0]:
                candidates[event_id] = (
                    rank,
                    event,
                    game,
                    path,
                )

    results: list[dict[str, Any]] = []

    for event_id, (_, event, game, source_path) in candidates.items():
        home_key = _team_key(event.get("home_team"))

        for book in event.get("bookmakers", []):
            book_key = str(book.get("key", ""))
            book_title = str(book.get("title") or book_key)

            for market in book.get("markets", []):
                market_key = str(market.get("key", ""))

                for outcome in market.get("outcomes", []):
                    name = str(outcome.get("name", ""))
                    description = str(
                        outcome.get("description") or ""
                    )
                    line = outcome.get("point")
                    price = outcome.get("price")

                    try:
                        american_odds = int(price)
                    except (TypeError, ValueError):
                        continue

                    if market_key in {"h2h", "spreads"}:
                        side = (
                            "home"
                            if _team_key(name) == home_key
                            else "away"
                        )
                    else:
                        side = name.lower()

                    selection = description or name
                    player_name = (
                        description
                        if market_key.startswith("player_")
                        else None
                    )

                    identity = "|".join(
                        (
                            event_id,
                            book_key,
                            market_key,
                            description,
                            name,
                            str(line),
                        )
                    )

                    market_id = hashlib.sha256(
                        identity.encode("utf-8")
                    ).hexdigest()[:20]

                    results.append(
                        {
                            "market_id": market_id,
                            "recommendation_id": market_id,
                            "model_ready": False,
                            "event_id": event_id,
                            "canonical_game_id":
                                game.get("canonical_game_id"),
                            "away_team": event.get("away_team"),
                            "home_team": event.get("home_team"),
                            "bookmaker_key": book_key,
                            "bookmaker_title": book_title,
                            "market_key": market_key,
                            "market_title":
                                market_key.replace("_", " ").title(),
                            "selection": selection,
                            "player_name": player_name,
                            "side": side,
                            "line": line,
                            "american_odds": american_odds,
                            "source_file": str(source_path),
                        }
                    )

    results.sort(
        key=lambda item: (
            item["market_key"],
            item["selection"],
            item["bookmaker_title"],
            item["side"],
            item["line"] if item["line"] is not None else -999,
        )
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "odds_format": "american",
        "count": len(results),
        "markets": results,
    }
