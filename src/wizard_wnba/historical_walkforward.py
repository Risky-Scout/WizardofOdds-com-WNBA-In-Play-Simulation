from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import time
from typing import Any, Iterable, Mapping, Sequence

import httpx

from wnba_inplay.simulation import (
    InPlaySimulator,
    PlayerEventProfile,
    TeamSimulationProfile,
    combination_pmf,
    game_market_pmf,
)
from wnba_inplay.rotation import PlayerRotationProfile
from wnba_inplay.domain import GameConfig, GameState as PossessionGameState, PlayerState, PlayerStats

from .domain import (
    GameState,
    PlayerLiveState,
    PlayerRateProfile,
)
from .simulation import MonteCarloEngine
from .oos_validation import ValidationThresholds, evaluate_oos_rows


SUPPORTED_MARKETS = (
    "h2h",
    "spreads",
    "totals",
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
)

PLAYER_MARKET_STATS: dict[str, tuple[str, ...]] = {
    "player_points": ("points",),
    "player_rebounds": ("rebounds",),
    "player_assists": ("assists",),
    "player_threes": ("threes",),
    "player_points_rebounds_assists": ("points", "rebounds", "assists"),
}

# Game-clock checkpoints. Wall-clock offsets approximate normal WNBA elapsed time.
# Poorly aligned snapshots are retained in the raw archive but excluded from
# promotion if the returned Odds API snapshot differs materially.
CHECKPOINTS = (
    # regulation elapsed seconds, expected wall minutes after tip
    (600, 30),    # end Q1
    (1200, 60),   # halftime
    (1800, 90),   # end Q3
    (2100, 105),  # 5:00 Q4
    (2280, 115),  # 2:00 Q4
)


class HistoricalPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoricalConfig:
    data_dir: Path
    seasons: tuple[int, ...] = (2024, 2025, 2026)
    sport_key: str = "basketball_wnba"
    regions: str = "us"
    bookmakers: tuple[str, ...] = (
        "draftkings",
        "fanduel",
        "caesars",
        "betmgm",
        "betrivers",
        "fanatics",
        "bovada",
    )
    markets: tuple[str, ...] = SUPPORTED_MARKETS
    historical_simulations: int = 1000
    seed: int = 20260715
    request_pause_seconds: float = 0.12
    max_games: int = 0
    minimum_rows: int = 1000
    minimum_selected_bets: int = 200
    bootstrap_samples: int = 5000
    maximum_snapshot_alignment_seconds: int = 360


@dataclass(frozen=True)
class BetaParameters:
    a: float
    b: float
    c: float
    sample_size: int
    log_loss: float
    brier: float


@dataclass
class PlayerHistory:
    rows: list[dict[str, Any]]

    def prior_rows(self, before: datetime, limit: int = 20) -> list[dict[str, Any]]:
        selected = [
            row
            for row in self.rows
            if parse_datetime(row["_game_date"]) < before
        ]
        return selected[-limit:]


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def clip(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_minutes(value: Any) -> float:
    text = str(value or "0").strip()
    if ":" in text:
        minute, second = text.split(":", 1)
        return safe_float(minute) + safe_float(second) / 60.0
    return max(0.0, safe_float(text))


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def team_key(value: Any) -> str:
    words = normalize_name(value).split()
    return words[-1] if words else ""


def american_to_decimal(american: int | float) -> float:
    value = float(american)
    if value == 0:
        raise ValueError("American odds cannot be zero")
    return 1.0 + (value / 100.0 if value > 0 else 100.0 / abs(value))


def implied_probability(american: int | float) -> float:
    return 1.0 / american_to_decimal(american)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    ordered = sorted(float(value) for value in values)
    position = clip(q, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def deterministic_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], 16) % (2**31)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, namespace: str, key: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", key)
        path = self.root / namespace / f"{safe}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def load(self, namespace: str, key: str) -> Any | None:
        path = self.path(namespace, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, namespace: str, key: str, payload: Any) -> Path:
        path = self.path(namespace, key)
        # Give every process a unique temporary file. This prevents
        # resumable or overlapping runs from racing over one .tmp path.
        temp = path.with_name(
            f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )

        try:
            temp.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

        return path


class HistoricalApi:
    def __init__(
        self,
        *,
        bdl_key: str,
        odds_key: str,
        cache: JsonCache,
        pause_seconds: float,
    ) -> None:
        self.bdl_key = bdl_key
        self.odds_key = odds_key
        self.cache = cache
        self.pause_seconds = pause_seconds
        self.client = httpx.Client(
            timeout=httpx.Timeout(45.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": "WizardofOdds-WNBA-WalkForward/1.0"},
        )
        self.odds_quota_remaining: int | None = None
        self.odds_quota_used: int | None = None

    def close(self) -> None:
        self.client.close()

    def _request(
        self,
        url: str,
        *,
        params: Sequence[tuple[str, str]] | Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        attempts: int = 6,
    ) -> tuple[Any, dict[str, str]]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.client.get(url, params=params, headers=headers)
                if response.status_code == 429:
                    delay = max(2.0, min(30.0, 2.0 ** attempt))
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                normalized_headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                }
                if "x-requests-remaining" in normalized_headers:
                    self.odds_quota_remaining = safe_int(
                        normalized_headers["x-requests-remaining"]
                    )
                if "x-requests-used" in normalized_headers:
                    self.odds_quota_used = safe_int(
                        normalized_headers["x-requests-used"]
                    )
                if self.pause_seconds > 0:
                    time.sleep(self.pause_seconds)
                return response.json(), normalized_headers
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(min(10.0, 1.5 ** attempt))
        raise HistoricalPipelineError(f"API request failed: {url}: {last_error}")

    def bdl_paginated(
        self,
        endpoint: str,
        *,
        params: list[tuple[str, str]],
        cache_key: str,
    ) -> list[dict[str, Any]]:
        cached = self.cache.load("bdl", cache_key)
        if isinstance(cached, list):
            return [dict(row) for row in cached]

        output: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = list(params)
            query.append(("per_page", "100"))
            if cursor:
                query.append(("cursor", cursor))
            payload, _ = self._request(
                f"https://api.balldontlie.io/wnba/v1/{endpoint}",
                params=query,
                headers={"Authorization": self.bdl_key},
            )
            rows = payload.get("data", []) if isinstance(payload, Mapping) else []
            output.extend(dict(row) for row in rows if isinstance(row, Mapping))
            meta = payload.get("meta", {}) if isinstance(payload, Mapping) else {}
            next_cursor = meta.get("next_cursor")
            if next_cursor in (None, "", cursor):
                break
            cursor = str(next_cursor)

        self.cache.save("bdl", cache_key, output)
        return output

    def bdl_game_payload(self, game_id: int) -> dict[str, Any]:
        key = f"game_{game_id}"
        cached = self.cache.load("bdl_game", key)
        if isinstance(cached, Mapping):
            return dict(cached)

        stats = self.bdl_paginated(
            "player_stats",
            params=[("game_ids[]", str(game_id))],
            cache_key=f"player_stats_game_{game_id}",
        )
        plays_payload, _ = self._request(
            "https://api.balldontlie.io/wnba/v1/plays",
            params={"game_id": str(game_id)},
            headers={"Authorization": self.bdl_key},
        )
        plays = (
            plays_payload.get("data", [])
            if isinstance(plays_payload, Mapping)
            else []
        )
        payload = {"player_stats": stats, "plays": plays}
        self.cache.save("bdl_game", key, payload)
        return payload

    def historical_events(
        self,
        *,
        sport_key: str,
        at: datetime,
        commence_from: datetime,
        commence_to: datetime,
    ) -> list[dict[str, Any]]:
        key = f"{sport_key}_{at.strftime('%Y%m%dT%H%M')}"
        cached = self.cache.load("odds_events", key)
        if isinstance(cached, list):
            return [dict(row) for row in cached]

        payload, _ = self._request(
            f"https://api.the-odds-api.com/v4/historical/sports/{sport_key}/events",
            params={
                "apiKey": self.odds_key,
                "date": iso_z(at),
                "dateFormat": "iso",
                "commenceTimeFrom": iso_z(commence_from),
                "commenceTimeTo": iso_z(commence_to),
            },
        )
        rows = payload.get("data", []) if isinstance(payload, Mapping) else []
        output = [dict(row) for row in rows if isinstance(row, Mapping)]
        self.cache.save("odds_events", key, output)
        return output

    def historical_event_odds(
        self,
        *,
        sport_key: str,
        event_id: str,
        at: datetime,
        regions: str,
        markets: Sequence[str],
        bookmakers: Sequence[str],
    ) -> dict[str, Any]:
        key = (
            f"{event_id}_{at.strftime('%Y%m%dT%H%M')}_"
            f"{hashlib.sha1(','.join(markets).encode()).hexdigest()[:8]}"
        )
        cached = self.cache.load("odds_snapshots", key)
        if isinstance(cached, Mapping):
            return dict(cached)

        payload, headers = self._request(
            (
                "https://api.the-odds-api.com/v4/historical/sports/"
                f"{sport_key}/events/{event_id}/odds"
            ),
            params={
                "apiKey": self.odds_key,
                "date": iso_z(at),
                "regions": regions,
                "markets": ",".join(markets),
                "bookmakers": ",".join(bookmakers),
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
        )
        output = {
            "payload": payload,
            "headers": headers,
            "requested_at": iso_z(at),
        }
        self.cache.save("odds_snapshots", key, output)
        return output


def game_datetime(game: Mapping[str, Any]) -> datetime:
    return parse_datetime(game.get("date"))


def game_home_name(game: Mapping[str, Any]) -> str:
    team = game.get("home_team", {})
    return str(team.get("full_name") or team.get("name") or "")


def game_away_name(game: Mapping[str, Any]) -> str:
    team = game.get("visitor_team", game.get("away_team", {}))
    return str(team.get("full_name") or team.get("name") or "")


def game_is_final(game: Mapping[str, Any]) -> bool:
    status = str(game.get("status") or "").lower()
    return (
        status in {"post", "final"}
        or status.startswith("final")
        or (
            safe_int(game.get("period")) >= 4
            and safe_int(game.get("home_score")) >= 0
            and safe_int(game.get("away_score")) >= 0
            and status not in {"scheduled", "in", "live"}
        )
    )


def match_event(
    game: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    home_key = team_key(game_home_name(game))
    away_key = team_key(game_away_name(game))
    matches = [
        event
        for event in events
        if team_key(event.get("home_team")) == home_key
        and team_key(event.get("away_team")) == away_key
    ]
    if len(matches) == 1:
        return matches[0]
    # Toronto Tempo can appear as "Tempo" or "Toronto Tempo".
    reverse = [
        event
        for event in events
        if {team_key(event.get("home_team")), team_key(event.get("away_team"))}
        == {home_key, away_key}
    ]
    return reverse[0] if len(reverse) == 1 else None


def unwrap_odds_event(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any] | None, datetime | None]:
    outer = snapshot.get("payload")
    if not isinstance(outer, Mapping):
        return None, None
    timestamp = outer.get("timestamp")
    data = outer.get("data")
    if isinstance(data, Mapping):
        event = dict(data)
    elif isinstance(data, list) and data and isinstance(data[0], Mapping):
        event = dict(data[0])
    else:
        event = None
    parsed = parse_datetime(timestamp) if timestamp else None
    return event, parsed


def play_clock_seconds(value: Any) -> int:
    text = str(value or "0").strip()
    if ":" in text:
        minute, second = text.split(":", 1)
        return max(0, safe_int(minute) * 60 + safe_int(second))
    return max(0, safe_int(text))


def play_elapsed_seconds(play: Mapping[str, Any]) -> int:
    period = max(1, safe_int(play.get("period"), 1))
    clock = play_clock_seconds(play.get("clock"))
    if period <= 4:
        return (period - 1) * 600 + max(0, 600 - clock)
    return 2400 + (period - 5) * 300 + max(0, 300 - clock)


def target_period_clock(elapsed_seconds: int) -> tuple[int, int]:
    elapsed = max(0, int(elapsed_seconds))
    if elapsed < 2400:
        period = min(4, elapsed // 600 + 1)
        within = elapsed - (period - 1) * 600
        return period, max(0, 600 - within)
    overtime_elapsed = elapsed - 2400
    period = 5 + overtime_elapsed // 300
    within = overtime_elapsed - (period - 5) * 300
    return period, max(0, 300 - within)


def latest_play_at(
    plays: Sequence[Mapping[str, Any]],
    elapsed_seconds: int,
) -> Mapping[str, Any] | None:
    accepted = [
        play
        for play in plays
        if play_elapsed_seconds(play) <= elapsed_seconds
    ]
    if not accepted:
        return None
    return max(
        accepted,
        key=lambda play: (
            play_elapsed_seconds(play),
            safe_int(play.get("order")),
        ),
    )


def final_player_name(row: Mapping[str, Any]) -> str:
    player = row.get("player", {})
    return (
        f"{player.get('first_name', '')} {player.get('last_name', '')}"
    ).strip()


def player_id(row: Mapping[str, Any]) -> str:
    player = row.get("player", {})
    return f"bdl-player-{player.get('id')}"


def row_team_name(row: Mapping[str, Any]) -> str:
    team = row.get("team", {})
    return str(team.get("full_name") or team.get("name") or "")


def match_players_in_text(
    text: str,
    names: Sequence[tuple[str, str]],
) -> list[str]:
    normalized = f" {normalize_name(text)} "
    found: list[tuple[int, str]] = []
    for name, identifier in names:
        target = normalize_name(name)
        if target and f" {target} " in normalized:
            found.append((len(target), identifier))
    found.sort(reverse=True)
    output: list[str] = []
    seen: set[str] = set()
    for _, identifier in found:
        if identifier not in seen:
            output.append(identifier)
            seen.add(identifier)
    return output


def reconstruct_box_score(
    plays: Sequence[Mapping[str, Any]],
    final_rows: Sequence[Mapping[str, Any]],
    elapsed_seconds: int,
) -> dict[str, dict[str, int]]:
    names = [
        (final_player_name(row), player_id(row))
        for row in final_rows
        if final_player_name(row)
    ]
    box: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "points": 0,
            "rebounds": 0,
            "assists": 0,
            "threes": 0,
            "steals": 0,
            "blocks": 0,
            "turnovers": 0,
            "fouls": 0,
        }
    )

    for play in sorted(
        (
            play
            for play in plays
            if play_elapsed_seconds(play) <= elapsed_seconds
        ),
        key=lambda play: (
            play_elapsed_seconds(play),
            safe_int(play.get("order")),
        ),
    ):
        text = str(play.get("text") or "")
        kind = normalize_name(play.get("type"))
        matched = match_players_in_text(text, names)
        if not matched:
            continue

        primary = matched[0]
        if bool(play.get("scoring_play")):
            points = max(0, safe_int(play.get("score_value")))
            if points:
                box[primary]["points"] += points
                if points == 3 or "three point" in kind or "3 point" in kind:
                    box[primary]["threes"] += 1

        combined = f"{kind} {normalize_name(text)}"
        if "rebound" in combined:
            box[primary]["rebounds"] += 1
        if "turnover" in combined:
            box[primary]["turnovers"] += 1
        if "steal" in combined:
            box[primary]["steals"] += 1
        if "block" in combined:
            box[primary]["blocks"] += 1
        if "foul" in combined:
            box[primary]["fouls"] += 1
        if "assist" in combined and len(matched) >= 2:
            box[matched[-1]]["assists"] += 1

    return dict(box)


def history_key(row: Mapping[str, Any]) -> str:
    return player_id(row)


def annotate_final_rows(
    game: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["_game_date"] = iso_z(game_datetime(game))
        item["_team_name"] = row_team_name(row)
        item["_game_id"] = str(game.get("id"))
        output.append(item)
    return output


def aggregate_prior(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    totals = defaultdict(float)
    games = 0
    minutes_values: list[float] = []
    for row in rows:
        minutes = parse_minutes(row.get("min"))
        if minutes <= 0:
            continue
        games += 1
        minutes_values.append(minutes)
        totals["minutes"] += minutes
        for key in (
            "pts",
            "reb",
            "ast",
            "fg3m",
            "fgm",
            "fga",
            "fg3a",
            "ftm",
            "fta",
            "oreb",
            "dreb",
            "turnover",
            "stl",
            "blk",
            "pf",
        ):
            totals[key] += safe_float(row.get(key))
    if games == 0:
        return {
            "games": 0.0,
            "minutes": 0.0,
            "minutes_sd": 6.0,
        }
    totals["games"] = float(games)
    totals["minutes_avg"] = totals["minutes"] / games
    totals["minutes_sd"] = (
        statistics.pstdev(minutes_values)
        if len(minutes_values) > 1
        else 4.0
    )
    return dict(totals)


def rolling_profiles_for_game(
    *,
    game: Mapping[str, Any],
    final_rows: Sequence[Mapping[str, Any]],
    histories: Mapping[str, PlayerHistory],
    box: Mapping[str, Mapping[str, int]],
    elapsed_seconds: int,
) -> tuple[
    dict[str, PlayerLiveState],
    dict[str, PlayerRateProfile],
    bool,
]:
    game_time = game_datetime(game)
    home = game_home_name(game)
    away = game_away_name(game)
    teams = {home, away}

    # Known-at-checkpoint roster: anyone already appearing in the PBP, plus
    # players whose recent prior team matches. Final-game participants are used
    # only if needed to construct a legal historical replay and are tagged as
    # availability proxies for bias analysis.
    roster_ids: set[str] = set(box)
    row_by_id = {player_id(row): row for row in final_rows}
    availability_proxy = False

    prior_team_candidates: dict[str, list[tuple[float, str]]] = {
        home: [],
        away: [],
    }
    for identifier, history in histories.items():
        prior_rows = history.prior_rows(game_time, limit=10)
        if not prior_rows:
            continue
        latest_team = str(prior_rows[-1].get("_team_name") or "")
        if latest_team not in teams:
            continue
        prior = aggregate_prior(prior_rows)
        prior_team_candidates[latest_team].append(
            (prior.get("minutes_avg", 0.0), identifier)
        )

    for team_name, candidates in prior_team_candidates.items():
        for _, identifier in sorted(candidates, reverse=True)[:10]:
            roster_ids.add(identifier)

    team_counts = defaultdict(int)
    for identifier in roster_ids:
        row = row_by_id.get(identifier)
        if row:
            team_counts[row_team_name(row)] += 1
        else:
            history = histories.get(identifier)
            prior_rows = history.prior_rows(game_time, limit=1) if history else []
            if prior_rows:
                team_counts[str(prior_rows[-1].get("_team_name"))] += 1

    for team_name in teams:
        if team_counts[team_name] >= 7:
            continue
        availability_proxy = True
        participants = [
            player_id(row)
            for row in final_rows
            if row_team_name(row) == team_name
        ]
        for identifier in participants:
            roster_ids.add(identifier)
            team_counts[team_name] += 1
            if team_counts[team_name] >= 8:
                break

    live_players: dict[str, PlayerLiveState] = {}
    profiles: dict[str, PlayerRateProfile] = {}
    elapsed_fraction = clip(elapsed_seconds / 2400.0, 0.0, 1.0)
    elapsed_minutes = elapsed_seconds / 60.0

    for identifier in sorted(roster_ids):
        final_row = row_by_id.get(identifier)
        history = histories.get(identifier, PlayerHistory([]))
        prior_rows = history.prior_rows(game_time, limit=20)
        prior = aggregate_prior(prior_rows)

        if final_row is not None:
            name = final_player_name(final_row)
            team_name = row_team_name(final_row)
        elif prior_rows:
            latest = prior_rows[-1]
            name = final_player_name(latest)
            team_name = str(latest.get("_team_name") or "")
        else:
            continue

        if team_name not in teams:
            continue

        current = box.get(identifier, {})
        target_minutes = clip(
            prior.get("minutes_avg", 24.0) if prior.get("games", 0) else 20.0,
            4.0,
            38.0,
        )
        estimated_minutes = clip(
            target_minutes * elapsed_fraction,
            0.0,
            elapsed_minutes,
        )
        games = max(prior.get("games", 0.0), 1.0)
        total_minutes = max(prior.get("minutes", 0.0), 1.0)

        league = {
            "points": 0.45,
            "rebounds": 0.20,
            "assists": 0.11,
            "threes": 0.045,
        }
        rates = {
            "points": (
                prior.get("pts", 0.0) + league["points"] * 120.0
            ) / (total_minutes + 120.0),
            "rebounds": (
                prior.get("reb", 0.0) + league["rebounds"] * 120.0
            ) / (total_minutes + 120.0),
            "assists": (
                prior.get("ast", 0.0) + league["assists"] * 120.0
            ) / (total_minutes + 120.0),
            "threes": (
                prior.get("fg3m", 0.0) + league["threes"] * 120.0
            ) / (total_minutes + 120.0),
        }
        role_uncertainty = clip(0.12 / math.sqrt(games), 0.02, 0.10)

        live_players[identifier] = PlayerLiveState(
            canonical_player_id=identifier,
            display_name=name,
            team=team_name,
            on_court=False,
            active=True,
            minutes_played=estimated_minutes,
            current_stint_seconds=0.0,
            fouls=safe_int(current.get("fouls")),
            injury_state="healthy",
            points=safe_int(current.get("points")),
            rebounds=safe_int(current.get("rebounds")),
            assists=safe_int(current.get("assists")),
            threes=safe_int(current.get("threes")),
            steals=safe_int(current.get("steals")),
            blocks=safe_int(current.get("blocks")),
            turnovers=safe_int(current.get("turnovers")),
        )
        profiles[identifier] = PlayerRateProfile(
            canonical_player_id=identifier,
            expected_remaining_minutes=max(
                0.0,
                target_minutes - estimated_minutes,
            ),
            remaining_minutes_sd=clip(
                prior.get("minutes_sd", 5.0),
                1.0,
                9.0,
            ),
            points_per_minute=clip(rates["points"], 0.05, 1.10),
            rebounds_per_minute=clip(rates["rebounds"], 0.02, 0.70),
            assists_per_minute=clip(rates["assists"], 0.01, 0.50),
            threes_per_minute=clip(rates["threes"], 0.0, 0.25),
            usage_multiplier=1.0,
            pace_multiplier=1.0,
            role_uncertainty=role_uncertainty,
            target_total_minutes=target_minutes,
        )

    return live_players, profiles, availability_proxy


def build_game_state(
    *,
    game: Mapping[str, Any],
    plays: Sequence[Mapping[str, Any]],
    elapsed_seconds: int,
    snapshot_time: datetime,
) -> GameState:
    latest = latest_play_at(plays, elapsed_seconds)
    period, clock = target_period_clock(elapsed_seconds)
    home_score = 0
    away_score = 0
    sequence = 0
    if latest is not None:
        period = max(1, safe_int(latest.get("period"), period))
        clock = play_clock_seconds(latest.get("clock"))
        home_score = safe_int(latest.get("home_score"))
        away_score = safe_int(latest.get("away_score"))
        sequence = safe_int(latest.get("order"))

    return GameState(
        canonical_game_id=f"bdl-{game['id']}",
        source_game_id=str(game["id"]),
        home_team=game_home_name(game),
        away_team=game_away_name(game),
        period=period,
        clock_seconds=float(clock),
        home_score=home_score,
        away_score=away_score,
        possession_team=None,
        event_sequence=sequence,
        source_timestamp=snapshot_time,
        received_timestamp=snapshot_time,
        status="in_progress",
        reconciliation_ok=True,
    )


def build_possession_state(
    *,
    game_state: GameState,
    live_players: Mapping[str, PlayerLiveState],
    profiles: Mapping[str, PlayerRateProfile],
    snapshot_time: datetime,
) -> tuple[
    PossessionGameState,
    dict[str, PlayerRotationProfile],
    dict[str, PlayerEventProfile],
    dict[str, TeamSimulationProfile],
]:
    config = GameConfig(
        game_id=game_state.canonical_game_id,
        home_team=game_state.home_team,
        away_team=game_state.away_team,
    )
    state = PossessionGameState(
        config=config,
        sequence=game_state.event_sequence,
        period=game_state.period,
        clock_seconds=int(round(game_state.clock_seconds)),
        home_score=game_state.home_score,
        away_score=game_state.away_score,
        status="in_progress",
        last_source_timestamp_ms=int(snapshot_time.timestamp() * 1000),
        last_received_timestamp_ms=int(snapshot_time.timestamp() * 1000),
    )
    team_players: dict[str, list[str]] = defaultdict(list)
    for identifier, live in live_players.items():
        state.players[identifier] = PlayerState(
            player_id=identifier,
            team_id=live.team,
            active=live.active,
            on_court=False,
            injury_state=live.injury_state,
            stats=PlayerStats(
                points=live.points,
                rebounds=live.rebounds,
                assists=live.assists,
                threes=live.threes,
                steals=live.steals,
                blocks=live.blocks,
                turnovers=live.turnovers,
                fouls=live.fouls,
                minutes_seconds=int(round(live.minutes_played * 60)),
            ),
        )
        team_players[live.team].append(identifier)

    for team_name in (game_state.home_team, game_state.away_team):
        candidates = sorted(
            team_players[team_name],
            key=lambda identifier: (
                live_players[identifier].minutes_played,
                profiles[identifier].target_total_minutes or 0.0,
            ),
            reverse=True,
        )
        if len(candidates) < 5:
            raise HistoricalPipelineError(
                f"Historical replay has fewer than five players for {team_name}"
            )
        lineup = tuple(candidates[:5])
        if team_name == game_state.home_team:
            state.home_lineup = lineup
        else:
            state.away_lineup = lineup
        for identifier in lineup:
            state.players[identifier].on_court = True

    rotation_profiles: dict[str, PlayerRotationProfile] = {}
    player_profiles: dict[str, PlayerEventProfile] = {}

    for team_name, identifiers in team_players.items():
        team_rates = [
            profiles[identifier].points_per_minute
            for identifier in identifiers
        ]
        mean_points_rate = statistics.mean(team_rates) if team_rates else 0.45
        for rank, identifier in enumerate(
            sorted(
                identifiers,
                key=lambda item: profiles[item].target_total_minutes or 0.0,
                reverse=True,
            )
        ):
            prior = profiles[identifier]
            rotation_profiles[identifier] = PlayerRotationProfile(
                player_id=identifier,
                target_total_minutes=prior.target_total_minutes or 20.0,
                rotation_weight=1.0,
                closing_priority=1.0 if rank < 5 else 0.15,
                foul_sensitivity=1.0,
                blowout_sensitivity=1.0,
            )
            usage = clip(
                prior.points_per_minute / max(mean_points_rate, 1e-9),
                0.45,
                2.4,
            )
            three_share = clip(
                prior.threes_per_minute * 2.7
                / max(prior.points_per_minute, 0.08),
                0.05,
                0.75,
            )
            player_profiles[identifier] = PlayerEventProfile(
                player_id=identifier,
                usage_weight=usage,
                three_point_share=three_share,
                two_point_pct=0.50,
                three_point_pct=clip(
                    0.32 + 0.20 * (prior.threes_per_minute - 0.04),
                    0.24,
                    0.48,
                ),
                free_throw_pct=0.80,
                turnover_probability=0.12,
                shooting_foul_probability=0.12,
                assist_weight=clip(
                    0.7 + prior.assists_per_minute * 5.0,
                    0.35,
                    3.0,
                ),
                offensive_rebound_weight=clip(
                    0.6 + prior.rebounds_per_minute * 2.0,
                    0.3,
                    3.5,
                ),
                defensive_rebound_weight=clip(
                    0.8 + prior.rebounds_per_minute * 3.0,
                    0.3,
                    3.5,
                ),
                steal_probability=0.02,
                block_probability=0.015,
            )

    team_profiles = {
        game_state.home_team: TeamSimulationProfile(
            team_id=game_state.home_team,
            pace_per_40=78.0,
        ),
        game_state.away_team: TeamSimulationProfile(
            team_id=game_state.away_team,
            pace_per_40=78.0,
        ),
    }
    return state, rotation_profiles, player_profiles, team_profiles


def extract_event(snapshot: Mapping[str, Any]) -> Mapping[str, Any] | None:
    outer = snapshot.get("payload")
    if not isinstance(outer, Mapping):
        return None
    data = outer.get("data")
    if isinstance(data, Mapping):
        return data
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        return data[0]
    return None


def offer_identity(
    *,
    bookmaker: str,
    market_key: str,
    outcome: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    description = normalize_name(outcome.get("description"))
    name = normalize_name(outcome.get("name"))
    point = str(outcome.get("point"))
    return bookmaker, market_key, description or name, point


def flatten_offers(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    home = str(event.get("home_team") or "")
    away = str(event.get("away_team") or "")
    for bookmaker in event.get("bookmakers", []):
        book_key = str(bookmaker.get("key") or "")
        book_title = str(bookmaker.get("title") or book_key)
        book_update = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key") or "")
            market_update = market.get("last_update") or book_update
            for outcome in market.get("outcomes", []):
                try:
                    american = int(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(outcome.get("name") or "")
                description = str(outcome.get("description") or "")
                point = (
                    float(outcome["point"])
                    if outcome.get("point") is not None
                    else None
                )
                side = name.lower()
                if market_key in {"h2h", "spreads"}:
                    side = "home" if team_key(name) == team_key(home) else "away"
                output.append(
                    {
                        "bookmaker_key": book_key,
                        "bookmaker_title": book_title,
                        "market_key": market_key,
                        "name": name,
                        "description": description,
                        "line": point,
                        "american_odds": american,
                        "last_update": market_update,
                        "home_team": home,
                        "away_team": away,
                        "identity": offer_identity(
                            bookmaker=book_key,
                            market_key=market_key,
                            outcome=outcome,
                        ),
                    }
                )
    return output


def final_value(
    *,
    offer: Mapping[str, Any],
    game: Mapping[str, Any],
    final_rows_by_name: Mapping[str, Mapping[str, Any]],
) -> float | None:
    market = str(offer["market_key"])
    home_score = safe_int(game.get("home_score"))
    away_score = safe_int(game.get("away_score"))
    if market == "totals":
        return float(home_score + away_score)
    if market == "h2h":
        return 1.0 if home_score > away_score else 0.0
    if market == "spreads":
        return float(home_score - away_score)

    row = final_rows_by_name.get(normalize_name(offer.get("description")))
    if row is None:
        return None
    if market == "player_points":
        return safe_float(row.get("pts"))
    if market == "player_rebounds":
        return safe_float(row.get("reb"))
    if market == "player_assists":
        return safe_float(row.get("ast"))
    if market == "player_threes":
        return safe_float(row.get("fg3m"))
    if market == "player_points_rebounds_assists":
        return (
            safe_float(row.get("pts"))
            + safe_float(row.get("reb"))
            + safe_float(row.get("ast"))
        )
    return None


def price_offer_from_automated_bundle(
    *,
    offer: Mapping[str, Any],
    game_state: GameState,
    bundle: Any,
    name_to_id: Mapping[str, str],
) -> tuple[float, float, float] | None:
    engine = MonteCarloEngine()
    market = str(offer["market_key"])
    side = str(offer["name"]).lower()
    point = offer.get("line")

    if market.startswith("player_"):
        identifier = name_to_id.get(normalize_name(offer.get("description")))
        if identifier is None or point is None:
            return None
        estimate = engine.price_player_market(
            bundle=bundle,
            player_id=identifier,
            market_key=market,
            line=float(point),
            side=side,
            calibration_score=1.0,
            calibrator_id="historical-raw",
            model_version="historical-raw",
            calibration_se=0.0,
            model_se=0.0,
        )
        return estimate.p_win, estimate.p_push, estimate.p_loss

    if market == "totals":
        if point is None or side not in {"over", "under"}:
            return None
        estimate = engine.price_game_market(
            bundle=bundle,
            market_key="totals",
            line=float(point),
            side=side,
            calibration_score=1.0,
            calibrator_id="historical-raw",
            model_version="historical-raw",
            calibration_se=0.0,
            model_se=0.0,
        )
        return estimate.p_win, estimate.p_push, estimate.p_loss

    if market == "h2h":
        is_home = normalize_name(offer.get("name")) == normalize_name(game_state.home_team)
        estimate = engine.price_game_market(
            bundle=bundle,
            market_key="h2h",
            line=0.5,
            side="over" if is_home else "under",
            calibration_score=1.0,
            calibrator_id="historical-raw",
            model_version="historical-raw",
            calibration_se=0.0,
            model_se=0.0,
        )
        return estimate.p_win, estimate.p_push, estimate.p_loss

    if market == "spreads" and point is not None:
        is_home = normalize_name(offer.get("name")) == normalize_name(game_state.home_team)
        transformed_line = -float(point) if is_home else float(point)
        estimate = engine.price_game_market(
            bundle=bundle,
            market_key="spreads",
            line=transformed_line,
            side="over" if is_home else "under",
            calibration_score=1.0,
            calibrator_id="historical-raw",
            model_version="historical-raw",
            calibration_se=0.0,
            model_se=0.0,
        )
        return estimate.p_win, estimate.p_push, estimate.p_loss

    return None


def price_offer_from_possession_paths(
    *,
    offer: Mapping[str, Any],
    paths: Sequence[Any],
    state: PossessionGameState,
    name_to_id: Mapping[str, str],
) -> tuple[float, float, float] | None:
    market = str(offer["market_key"])
    side = str(offer["name"]).lower()
    point = offer.get("line")

    if market.startswith("player_"):
        identifier = name_to_id.get(normalize_name(offer.get("description")))
        if identifier is None or point is None:
            return None
        pmf = combination_pmf(paths, identifier, PLAYER_MARKET_STATS[market])
        opu = pmf.over_push_under(float(point))
        return (
            (opu.p_over, opu.p_push, opu.p_under)
            if side == "over"
            else (opu.p_under, opu.p_push, opu.p_over)
        )

    if market == "totals" and point is not None:
        pmf = game_market_pmf(paths, lambda path: path.game_total)
        opu = pmf.over_push_under(float(point))
        return (
            (opu.p_over, opu.p_push, opu.p_under)
            if side == "over"
            else (opu.p_under, opu.p_push, opu.p_over)
        )

    if market == "h2h":
        is_home = normalize_name(offer.get("name")) == normalize_name(state.config.home_team)
        home_wins = sum(path.home_score > path.away_score for path in paths)
        probability = home_wins / len(paths)
        selected = probability if is_home else 1.0 - probability
        return selected, 0.0, 1.0 - selected

    if market == "spreads" and point is not None:
        is_home = normalize_name(offer.get("name")) == normalize_name(state.config.home_team)
        values = [
            (path.home_margin if is_home else -path.home_margin)
            + float(point)
            for path in paths
        ]
        wins = sum(value > 0 for value in values)
        pushes = sum(math.isclose(value, 0.0) for value in values)
        return (
            wins / len(values),
            pushes / len(values),
            (len(values) - wins - pushes) / len(values),
        )

    return None


def settle_result(
    *,
    offer: Mapping[str, Any],
    value: float,
    game: Mapping[str, Any],
) -> str:
    market = str(offer["market_key"])
    point = offer.get("line")
    name = str(offer.get("name") or "").lower()

    if market == "h2h":
        is_home = normalize_name(offer.get("name")) == normalize_name(game_home_name(game))
        won = (value == 1.0) if is_home else (value == 0.0)
        return "win" if won else "loss"

    if market == "spreads":
        is_home = normalize_name(offer.get("name")) == normalize_name(game_home_name(game))
        adjusted = (value if is_home else -value) + safe_float(point)
        if math.isclose(adjusted, 0.0):
            return "push"
        return "win" if adjusted > 0 else "loss"

    if point is None:
        return "loss"
    if math.isclose(value, float(point)):
        return "push"
    if name == "over":
        return "win" if value > float(point) else "loss"
    if name == "under":
        return "win" if value < float(point) else "loss"
    return "loss"


def complementary_key(offer: Mapping[str, Any]) -> tuple[str, str, str]:
    market_key = str(offer["market_key"])
    line = offer.get("line")
    normalized_line = (
        str(abs(float(line)))
        if market_key == "spreads" and line is not None
        else str(line)
    )
    return (
        market_key,
        normalize_name(offer.get("description")),
        normalized_line,
    )


def leave_one_out_consensus(
    offers: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
) -> tuple[float | None, int, float | None]:
    key = complementary_key(target)
    target_book = str(target["bookmaker_key"])
    target_name = str(target.get("name") or "").lower()
    target_is_home = normalize_name(target.get("name")) == normalize_name(target.get("home_team"))

    by_book: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for offer in offers:
        if complementary_key(offer) != key:
            continue
        if str(offer["bookmaker_key"]) == target_book:
            continue
        by_book[str(offer["bookmaker_key"])].append(offer)

    probabilities: list[float] = []
    for book_offers in by_book.values():
        if len(book_offers) < 2:
            continue
        target_offer = None
        opposite_offer = None
        for offer in book_offers:
            name = str(offer.get("name") or "").lower()
            if str(target["market_key"]) in {"h2h", "spreads"}:
                is_home = normalize_name(offer.get("name")) == normalize_name(offer.get("home_team"))
                if is_home == target_is_home:
                    target_offer = offer
                else:
                    opposite_offer = offer
            else:
                if name == target_name:
                    target_offer = offer
                elif name in {"over", "under"}:
                    opposite_offer = offer
        if target_offer is None or opposite_offer is None:
            continue
        target_implied = implied_probability(target_offer["american_odds"])
        opposite_implied = implied_probability(opposite_offer["american_odds"])
        total = target_implied + opposite_implied
        if total > 0:
            probabilities.append(target_implied / total)

    if len(probabilities) < 2:
        return None, len(probabilities), None

    median = statistics.median(probabilities)
    mad = median_absolute_deviation(probabilities)
    if mad > 0:
        filtered = [
            probability
            for probability in probabilities
            if abs(probability - median) <= 2.5 * 1.4826 * mad
        ]
        if len(filtered) >= 2:
            probabilities = filtered
    dispersion = (
        statistics.pstdev(probabilities)
        if len(probabilities) > 1
        else 0.0
    )
    return statistics.median(probabilities), len(probabilities), dispersion


def _logit(value: float) -> float:
    p = clip(value, 1e-8, 1 - 1e-8)
    return math.log(p / (1 - p))


def solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular calibration system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(
                    augmented[row],
                    augmented[column],
                    strict=True,
                )
            ]
    return [augmented[index][3] for index in range(3)]


def fit_beta(rows: Sequence[Mapping[str, Any]]) -> BetaParameters:
    settled = [
        row
        for row in rows
        if row.get("raw_probability") is not None
        and row.get("binary_outcome") in {0, 1}
        and row.get("result") != "push"
    ]
    if len(settled) < 30:
        return BetaParameters(1.0, -1.0, 0.0, len(settled), 0.0, 0.0)

    coefficients = [1.0, -1.0, 0.0]
    ridge = 1e-4
    for _ in range(80):
        hessian = [[0.0] * 3 for _ in range(3)]
        gradient = [0.0] * 3
        for row in settled:
            raw = clip(float(row["raw_probability"]), 1e-8, 1 - 1e-8)
            features = [math.log(raw), math.log(1 - raw), 1.0]
            eta = clip(
                sum(coef * feature for coef, feature in zip(coefficients, features)),
                -35.0,
                35.0,
            )
            fitted = 1.0 / (1.0 + math.exp(-eta))
            outcome = int(row["binary_outcome"])
            residual = outcome - fitted
            variance = max(fitted * (1 - fitted), 1e-8)
            weight = max(0.0, safe_float(row.get("weight"), 1.0))
            for i in range(3):
                gradient[i] += weight * residual * features[i]
                for j in range(3):
                    hessian[i][j] += (
                        weight * variance * features[i] * features[j]
                    )
        for i in range(3):
            hessian[i][i] += ridge
        try:
            delta = solve_3x3(hessian, gradient)
        except ValueError:
            break
        coefficients = [
            coefficient + change
            for coefficient, change in zip(coefficients, delta, strict=True)
        ]
        if max(abs(change) for change in delta) < 1e-8:
            break

    probabilities = [
        apply_beta(float(row["raw_probability"]), coefficients)
        for row in settled
    ]
    outcomes = [int(row["binary_outcome"]) for row in settled]
    log_loss = statistics.mean(
        -(
            outcome * math.log(clip(probability, 1e-12, 1.0))
            + (1 - outcome) * math.log(clip(1 - probability, 1e-12, 1.0))
        )
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )
    brier = statistics.mean(
        (probability - outcome) ** 2
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )
    return BetaParameters(
        a=coefficients[0],
        b=coefficients[1],
        c=coefficients[2],
        sample_size=len(settled),
        log_loss=log_loss,
        brier=brier,
    )


def apply_beta(probability: float, parameters: Sequence[float]) -> float:
    p = clip(probability, 1e-10, 1 - 1e-10)
    a, b, c = map(float, parameters)
    score = clip(a * math.log(p) + b * math.log(1 - p) + c, -40.0, 40.0)
    return 1.0 / (1.0 + math.exp(-score))


def chronological_split(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sorted_rows = sorted(rows, key=lambda row: (row["game_date"], row["game_id"]))
    seasons = sorted({safe_int(row.get("season")) for row in sorted_rows})
    if len(seasons) >= 3:
        train_seasons = set(seasons[:-2])
        calibration_season = seasons[-2]
        oos_season = seasons[-1]
        train = [dict(row) for row in sorted_rows if row["season"] in train_seasons]
        calibration = [
            dict(row)
            for row in sorted_rows
            if row["season"] == calibration_season
        ]
        oos = [dict(row) for row in sorted_rows if row["season"] == oos_season]
        return train, calibration, oos

    games = sorted({(row["game_date"], row["game_id"]) for row in sorted_rows})
    train_cut = max(1, int(len(games) * 0.50))
    calibration_cut = max(train_cut + 1, int(len(games) * 0.75))
    train_games = set(games[:train_cut])
    calibration_games = set(games[train_cut:calibration_cut])
    oos_games = set(games[calibration_cut:])
    return (
        [dict(row) for row in sorted_rows if (row["game_date"], row["game_id"]) in train_games],
        [
            dict(row)
            for row in sorted_rows
            if (row["game_date"], row["game_id"]) in calibration_games
        ],
        [dict(row) for row in sorted_rows if (row["game_date"], row["game_id"]) in oos_games],
    )


def calibrators_by_market(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, BetaParameters]:
    global_parameters = fit_beta(rows)
    result = {"__global__": global_parameters}
    markets = sorted({str(row["market_key"]) for row in rows})
    for market in markets:
        subset = [row for row in rows if row["market_key"] == market]
        parameters = fit_beta(subset)
        result[market] = (
            parameters
            if parameters.sample_size >= 100
            else global_parameters
        )
    return result


def calibrated_rows(
    rows: Sequence[Mapping[str, Any]],
    calibrators: Mapping[str, BetaParameters],
    *,
    simulations: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    global_parameters = calibrators["__global__"]
    for raw in rows:
        row = dict(raw)
        parameters = calibrators.get(str(row["market_key"]), global_parameters)
        probability = apply_beta(
            float(row["raw_probability"]),
            (parameters.a, parameters.b, parameters.c),
        )
        consensus = row.get("consensus_probability")
        if consensus is not None:
            # Match the live optimizer: market anchor is stronger early and
            # decreases as game-state information accumulates.
            elapsed_fraction = clip(float(row["elapsed_seconds"]) / 2400.0, 0.0, 1.0)
            consensus_weight = clip(0.65 - 0.40 * elapsed_fraction, 0.20, 0.65)
            probability = (
                (1 - consensus_weight) * probability
                + consensus_weight * float(consensus)
            )
            row["consensus_weight"] = consensus_weight
        else:
            row["consensus_weight"] = 0.0

        offered_decimal = float(row["offered_decimal_odds"])
        p_push = clip(float(row.get("push_probability", 0.0)), 0.0, 1.0)
        non_push = 1.0 - p_push
        p_win = probability * non_push
        p_loss = (1.0 - probability) * non_push

        calibration_error = math.sqrt(max(parameters.brier, 0.0) / max(parameters.sample_size, 1))
        mc_error = math.sqrt(max(p_win * (1 - p_win), 0.0) / max(simulations, 1))
        model_error = 0.025
        uncertainty = math.sqrt(
            calibration_error**2 + mc_error**2 + model_error**2
        )
        allowance = 1.645 * uncertainty
        conservative_win = max(0.0, p_win - allowance)
        conservative_loss = min(non_push, p_loss + allowance)
        expected_roi = p_win * (offered_decimal - 1.0) - p_loss
        conservative_roi = (
            conservative_win * (offered_decimal - 1.0)
            - conservative_loss
        )

        row["probability"] = probability
        row["expected_roi"] = expected_roi
        row["conservative_roi"] = conservative_roi
        row["total_uncertainty"] = uncertainty
        row["selected"] = bool(
            row.get("available", True)
            and not row.get("stale", False)
            and safe_int(row.get("consensus_book_count")) >= 3
            and conservative_roi >= 0.03
        )
        output.append(row)
    return output


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def profile_bundle_rows(
    histories: Mapping[str, PlayerHistory],
    through: datetime,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for identifier, history in sorted(histories.items()):
        prior_rows = history.prior_rows(through + timedelta(seconds=1), limit=20)
        prior = aggregate_prior(prior_rows)
        if prior.get("games", 0) < 1:
            continue
        total_minutes = max(prior.get("minutes", 0.0), 1.0)
        target = clip(prior.get("minutes_avg", 20.0), 4.0, 38.0)
        profiles.append(
            {
                "canonical_player_id": identifier,
                "expected_remaining_minutes": target / 2.0,
                "remaining_minutes_sd": clip(prior.get("minutes_sd", 5.0), 1.0, 9.0),
                "points_per_minute": clip(prior.get("pts", 0.0) / total_minutes, 0.05, 1.10),
                "rebounds_per_minute": clip(prior.get("reb", 0.0) / total_minutes, 0.02, 0.70),
                "assists_per_minute": clip(prior.get("ast", 0.0) / total_minutes, 0.01, 0.50),
                "threes_per_minute": clip(prior.get("fg3m", 0.0) / total_minutes, 0.0, 0.25),
                "usage_multiplier": 1.0,
                "pace_multiplier": 1.0,
                "role_uncertainty": clip(0.12 / math.sqrt(prior["games"]), 0.02, 0.10),
                "target_total_minutes": target,
            }
        )
    return profiles


class HistoricalWalkForwardPipeline:
    def __init__(self, config: HistoricalConfig) -> None:
        self.config = config
        root = config.data_dir / "historical_walkforward"
        self.cache = JsonCache(root / "cache")
        self.output_dir = root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        bdl_key = os.environ.get("BALLDONTLIE_API_KEY", "").strip()
        odds_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
        if not bdl_key or not odds_key:
            raise HistoricalPipelineError(
                "BALLDONTLIE_API_KEY and THE_ODDS_API_KEY must be configured"
            )
        self.api = HistoricalApi(
            bdl_key=bdl_key,
            odds_key=odds_key,
            cache=self.cache,
            pause_seconds=config.request_pause_seconds,
        )

    def close(self) -> None:
        self.api.close()

    def backfill_games(self) -> list[dict[str, Any]]:
        games: list[dict[str, Any]] = []
        for season in self.config.seasons:
            rows = self.api.bdl_paginated(
                "games",
                params=[("seasons[]", str(season))],
                cache_key=f"games_season_{season}",
            )
            games.extend(row for row in rows if game_is_final(row))
        unique = {str(game["id"]): game for game in games}
        ordered = sorted(unique.values(), key=game_datetime)
        if self.config.max_games > 0:
            ordered = ordered[-self.config.max_games :]
        return ordered

    def backfill_game(self, game: Mapping[str, Any]) -> dict[str, Any]:
        game_id = safe_int(game["id"])
        payload = self.api.bdl_game_payload(game_id)
        commence = game_datetime(game)
        event_query_at = commence - timedelta(minutes=20)
        events = self.api.historical_events(
            sport_key=self.config.sport_key,
            at=event_query_at,
            commence_from=commence - timedelta(hours=2),
            commence_to=commence + timedelta(hours=2),
        )
        event = match_event(game, events)
        snapshots: list[dict[str, Any]] = []
        if event is not None:
            event_id = str(event["id"])
            for elapsed_seconds, wall_minutes in CHECKPOINTS:
                requested = commence + timedelta(minutes=wall_minutes)
                snapshot = self.api.historical_event_odds(
                    sport_key=self.config.sport_key,
                    event_id=event_id,
                    at=requested,
                    regions=self.config.regions,
                    markets=self.config.markets,
                    bookmakers=self.config.bookmakers,
                )
                snapshots.append(
                    {
                        "elapsed_seconds": elapsed_seconds,
                        "wall_minutes": wall_minutes,
                        "snapshot": snapshot,
                    }
                )
        return {
            "game": dict(game),
            "event": dict(event) if event is not None else None,
            "player_stats": payload.get("player_stats", []),
            "plays": payload.get("plays", []),
            "snapshots": snapshots,
        }

    def raw_rows(self, games: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, PlayerHistory]]:
        histories: dict[str, PlayerHistory] = defaultdict(lambda: PlayerHistory([]))
        rows: list[dict[str, Any]] = []
        previous_offers_by_game: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {}

        for game_index, game in enumerate(games, start=1):
            game_id = str(game["id"])
            print(
                f"[replay {game_index}/{len(games)}] "
                f"{game_away_name(game)} at {game_home_name(game)} "
                f"{game_datetime(game).date()}",
                flush=True,
            )
            payload = self.backfill_game(game)
            final_rows = annotate_final_rows(game, payload["player_stats"])
            final_rows_by_name = {
                normalize_name(final_player_name(row)): row
                for row in final_rows
            }
            plays = [
                dict(play)
                for play in payload["plays"]
                if isinstance(play, Mapping)
            ]
            snapshots = payload["snapshots"]

            for checkpoint in snapshots:
                event, snapshot_time = unwrap_odds_event(checkpoint["snapshot"])
                if event is None or snapshot_time is None:
                    continue
                requested = parse_datetime(
                    checkpoint["snapshot"].get("requested_at")
                )
                alignment_seconds = abs(
                    (snapshot_time - requested).total_seconds()
                )
                if alignment_seconds > self.config.maximum_snapshot_alignment_seconds:
                    continue

                elapsed_seconds = safe_int(checkpoint["elapsed_seconds"])
                box = reconstruct_box_score(plays, final_rows, elapsed_seconds)
                game_state = build_game_state(
                    game=game,
                    plays=plays,
                    elapsed_seconds=elapsed_seconds,
                    snapshot_time=snapshot_time,
                )
                live_players, profiles, availability_proxy = rolling_profiles_for_game(
                    game=game,
                    final_rows=final_rows,
                    histories=histories,
                    box=box,
                    elapsed_seconds=elapsed_seconds,
                )
                if len(live_players) < 10 or set(live_players) != set(profiles):
                    continue

                automated_bundle = MonteCarloEngine().simulate(
                    game=game_state,
                    live_players=live_players,
                    profiles=profiles,
                    simulations=self.config.historical_simulations,
                    seed=deterministic_seed(
                        self.config.seed,
                        game_id,
                        elapsed_seconds,
                        "automated",
                    ),
                )

                try:
                    possession_state, rotations, player_events, team_profiles = (
                        build_possession_state(
                            game_state=game_state,
                            live_players=live_players,
                            profiles=profiles,
                            snapshot_time=snapshot_time,
                        )
                    )
                    possession_paths = InPlaySimulator().simulate(
                        possession_state,
                        rotations,
                        player_events,
                        team_profiles,
                        simulations=self.config.historical_simulations,
                        seed=deterministic_seed(
                            self.config.seed,
                            game_id,
                            elapsed_seconds,
                            "possession",
                        ),
                    )
                except Exception:
                    possession_paths = ()

                name_to_id = {
                    normalize_name(live.display_name): identifier
                    for identifier, live in live_players.items()
                }
                current_offers = flatten_offers(event)
                previous = previous_offers_by_game.get(game_id, {})
                current_by_identity = {
                    offer["identity"]: offer
                    for offer in current_offers
                }

                # Disappeared offers are retained as unavailable controls.
                controls: list[dict[str, Any]] = []
                for identity, old_offer in previous.items():
                    if identity not in current_by_identity:
                        control = dict(old_offer)
                        control["available"] = False
                        controls.append(control)

                all_offers: list[dict[str, Any]] = []
                for offer in current_offers:
                    item = dict(offer)
                    item["available"] = True
                    all_offers.append(item)
                all_offers.extend(controls)

                for offer in all_offers:
                    value = final_value(
                        offer=offer,
                        game=game,
                        final_rows_by_name=final_rows_by_name,
                    )
                    if value is None:
                        continue
                    automated = price_offer_from_automated_bundle(
                        offer=offer,
                        game_state=game_state,
                        bundle=automated_bundle,
                        name_to_id=name_to_id,
                    )
                    if automated is None:
                        continue
                    p_win, p_push, p_loss = automated
                    non_push = max(1 - p_push, 1e-12)
                    raw_probability = p_win / non_push

                    possession_probability = None
                    if possession_paths:
                        possession = price_offer_from_possession_paths(
                            offer=offer,
                            paths=possession_paths,
                            state=possession_state,
                            name_to_id=name_to_id,
                        )
                        if possession is not None:
                            possession_non_push = max(1 - possession[1], 1e-12)
                            possession_probability = possession[0] / possession_non_push

                    consensus, book_count, dispersion = leave_one_out_consensus(
                        current_offers,
                        offer,
                    )
                    update = offer.get("last_update")
                    stale = True
                    market_age = None
                    if update:
                        try:
                            market_age = max(
                                0.0,
                                (snapshot_time - parse_datetime(update)).total_seconds(),
                            )
                            stale = market_age > 90.0
                        except ValueError:
                            pass

                    result = settle_result(
                        offer=offer,
                        value=value,
                        game=game,
                    )
                    binary_outcome = None
                    if result == "win":
                        binary_outcome = 1
                    elif result == "loss":
                        binary_outcome = 0

                    offered_decimal = american_to_decimal(
                        offer["american_odds"]
                    )
                    row = {
                        "game_id": game_id,
                        "canonical_game_id": f"bdl-{game_id}",
                        "game_date": iso_z(game_datetime(game)),
                        "season": safe_int(game.get("season")),
                        "checkpoint_timestamp": iso_z(snapshot_time),
                        "elapsed_seconds": elapsed_seconds,
                        "period": game_state.period,
                        "clock_seconds": game_state.clock_seconds,
                        "home_score": game_state.home_score,
                        "away_score": game_state.away_score,
                        "market_key": offer["market_key"],
                        "bookmaker_key": offer["bookmaker_key"],
                        "selection": offer["description"] or offer["name"],
                        "side": offer["name"],
                        "line": offer["line"],
                        "american_odds": offer["american_odds"],
                        "offered_decimal_odds": offered_decimal,
                        "raw_probability": raw_probability,
                        "possession_raw_probability": possession_probability,
                        "push_probability": p_push,
                        "consensus_probability": consensus,
                        "consensus_book_count": book_count,
                        "consensus_dispersion": dispersion,
                        "final_value": value,
                        "result": result,
                        "binary_outcome": binary_outcome,
                        "outcome": binary_outcome,
                        "available": bool(offer.get("available", True)),
                        "stale": stale,
                        "market_age_seconds": market_age,
                        "availability_proxy": availability_proxy,
                        "snapshot_alignment_seconds": alignment_seconds,
                        "weight": 1.0,
                    }
                    rows.append(row)

                previous_offers_by_game[game_id] = current_by_identity

            for row in final_rows:
                histories[history_key(row)].rows.append(row)

        return rows, dict(histories)

    def run(self) -> dict[str, Any]:
        games = self.backfill_games()
        if not games:
            raise HistoricalPipelineError("No completed historical WNBA games found")

        print(
            f"Historical games: {len(games)} | seasons={self.config.seasons}",
            flush=True,
        )
        raw_rows, histories = self.raw_rows(games)
        raw_path = self.output_dir / "raw_replay_rows.jsonl"
        write_jsonl(raw_path, raw_rows)

        settled = [
            row
            for row in raw_rows
            if row.get("binary_outcome") in {0, 1}
        ]
        train, calibration, oos = chronological_split(settled)
        if not train or not calibration or not oos:
            raise HistoricalPipelineError(
                "Chronological train/calibration/OOS split is empty"
            )

        # True two-step walk forward:
        #   fit on earliest season(s), score next season;
        #   refit on earliest + next, score latest untouched season.
        validation_calibrators = calibrators_by_market(train)
        walkforward_validation = calibrated_rows(
            calibration,
            validation_calibrators,
            simulations=self.config.historical_simulations,
        )
        production_fit_rows = train + calibration
        production_calibrators = calibrators_by_market(production_fit_rows)
        oos_rows = calibrated_rows(
            oos,
            production_calibrators,
            simulations=self.config.historical_simulations,
        )

        write_jsonl(
            self.output_dir / "walkforward_validation_predictions.jsonl",
            walkforward_validation,
        )
        oos_path = self.config.data_dir / "calibration" / "oos_predictions.jsonl"
        write_jsonl(oos_path, oos_rows)

        thresholds = ValidationThresholds(
            minimum_rows=self.config.minimum_rows,
            minimum_selected_bets=self.config.minimum_selected_bets,
            bootstrap_samples=self.config.bootstrap_samples,
        )
        metrics = evaluate_oos_rows(oos_rows, thresholds=thresholds)
        report = {
            "generated_at": iso_z(datetime.now(UTC)),
            "seasons": list(self.config.seasons),
            "games": len(games),
            "raw_rows": len(raw_rows),
            "train_rows": len(train),
            "walkforward_validation_rows": len(calibration),
            "oos_rows": len(oos),
            "historical_simulations": self.config.historical_simulations,
            "odds_quota_remaining": self.api.odds_quota_remaining,
            "odds_quota_used": self.api.odds_quota_used,
            "alignment_method": (
                "fixed game-clock checkpoints paired to nearest five-minute "
                "historical odds snapshot; poor snapshot alignments excluded"
            ),
            "calibrators": {
                key: asdict(value)
                for key, value in production_calibrators.items()
            },
            "oos_metrics": metrics.to_dict(),
        }
        report_path = self.config.data_dir / "calibration" / "walkforward_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        validation = metrics.to_dict()
        calibration_score = clip(
            1.0
            - 0.50 * abs(metrics.calibration_slope - 1.0)
            - abs(metrics.calibration_intercept),
            0.0,
            1.0,
        )
        bundle = {
            "metadata": {
                "model_version": (
                    "wnba-rolling-rates-walkforward-"
                    f"{datetime.now(UTC).strftime('%Y%m%d')}"
                ),
                "calibrator_id": "beta-walkforward-v1",
                "calibration_score": calibration_score,
                "calibration_se": max(
                    0.005,
                    abs(metrics.calibration_intercept) / math.sqrt(max(metrics.sample_size, 1)),
                ),
                "model_se": 0.025,
                "calibration_parameters": {
                    market: [params.a, params.b, params.c]
                    for market, params in production_calibrators.items()
                    if market != "__global__"
                },
            },
            "trained_through": str(game_datetime(games[-1]).date()),
            "validation_report": {
                "oos_log_loss": statistics.mean(
                    -(
                        int(row["outcome"]) * math.log(clip(float(row["probability"]), 1e-12, 1.0))
                        + (1 - int(row["outcome"]))
                        * math.log(clip(1 - float(row["probability"]), 1e-12, 1.0))
                    )
                    for row in oos_rows
                ),
                "oos_brier": metrics.oos_brier,
                "calibration_intercept": metrics.calibration_intercept,
                "calibration_slope": metrics.calibration_slope,
                "sample_size": metrics.sample_size,
                "consensus_brier": metrics.consensus_brier,
                "after_vig_roi": metrics.after_vig_roi,
                "bootstrap_roi_lower_95": metrics.bootstrap_roi_lower_95,
                "stale_residual_bias": metrics.stale_residual_bias,
                "availability_residual_bias": metrics.availability_residual_bias,
                "promotion_passed": metrics.passed,
                "blockers": list(metrics.blockers),
            },
            "profiles": profile_bundle_rows(
                histories,
                game_datetime(games[-1]),
            ),
        }

        candidate_path = self.config.data_dir / "models" / "candidate.walkforward.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        marker = self.config.data_dir / "calibration" / "PROMOTION_APPROVED"
        production_path = self.config.data_dir / "models" / "production.json"
        if metrics.passed:
            production_path.write_text(
                json.dumps(bundle, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            marker.write_text("approved\n", encoding="utf-8")
        else:
            if marker.exists():
                marker.unlink()

        print(json.dumps(report, indent=2, sort_keys=True), flush=True)
        return report
