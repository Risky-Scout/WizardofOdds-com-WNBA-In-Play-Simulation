
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from wnba_inplay.contracts import MarketSpec, RunMetadata
from wnba_inplay.domain import (
    GameConfig,
    GameState,
    PlayerState,
    PlayerStats,
)
from wnba_inplay.fingerprint import fingerprint
from wnba_inplay.pricing import CalibrationStatus, QuoteContext
from wnba_inplay.rotation import PlayerRotationProfile
from wnba_inplay.service import SimulationRequest, SimulationService
from wnba_inplay.simulation import (
    PlayerEventProfile,
    TeamSimulationProfile,
)

from .odds_math import american_to_decimal


PLAYER_MARKET_STATS: dict[str, tuple[str, ...]] = {
    "player_points": ("points",),
    "player_rebounds": ("rebounds",),
    "player_assists": ("assists",),
    "player_threes": ("threes",),
    "player_points_rebounds_assists": (
        "points",
        "rebounds",
        "assists",
    ),
}


class LiveReferenceError(RuntimeError):
    pass


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_minutes(value: object) -> float:
    text = str(value or "0").strip()
    if ":" in text:
        minute, second = text.split(":", 1)
        return max(
            0.0,
            _number(minute) + _number(second) / 60.0,
        )
    return max(0.0, _number(text))


def _team_name(row: Mapping[str, Any]) -> str:
    team = row.get("team")
    if isinstance(team, Mapping):
        return str(
            team.get("full_name")
            or team.get("name")
            or team.get("abbreviation")
            or ""
        )
    return str(row.get("team_name") or "")


def _player_name(row: Mapping[str, Any]) -> str:
    player = row.get("player")
    if isinstance(player, Mapping):
        name = " ".join(
            part
            for part in (
                str(player.get("first_name") or "").strip(),
                str(player.get("last_name") or "").strip(),
            )
            if part
        )
        if name:
            return name
        return str(player.get("full_name") or player.get("name") or "")
    return str(row.get("player_name") or "")


def _player_id(row: Mapping[str, Any]) -> str:
    player = row.get("player")
    if isinstance(player, Mapping) and player.get("id") is not None:
        return f"bdl-player-{player['id']}"
    if row.get("player_id") is not None:
        return f"bdl-player-{row['player_id']}"
    name = _player_name(row)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"player-{digest}"


def _normalized_name(value: object) -> str:
    return " ".join(
        re.findall(r"[a-z0-9]+", str(value or "").lower())
    )


def _latest_player_rows(
    data_dir: Path,
    source_game_id: str,
) -> list[Mapping[str, Any]]:
    root = (
        data_dir
        / "raw"
        / "provider=balldontlie"
        / "endpoint=wnba_v1_player_stats"
    )
    if not root.exists():
        raise LiveReferenceError(
            "No archived BALLDONTLIE player-stat snapshots were found."
        )

    files = sorted(
        root.rglob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for path in files[:1000]:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        payload = (
            envelope.get("payload")
            if isinstance(envelope, Mapping)
            else None
        )
        if not isinstance(payload, Mapping):
            continue

        rows = payload.get("data", [])
        if not isinstance(rows, list) or not rows:
            continue

        matching: list[Mapping[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            game = row.get("game")
            row_game_id = (
                game.get("id")
                if isinstance(game, Mapping)
                else row.get("game_id")
            )
            if row_game_id is None or str(row_game_id) == source_game_id:
                matching.append(row)

        if matching:
            return matching

    raise LiveReferenceError(
        f"No player-stat snapshot matched game {source_game_id}."
    )


def _elapsed_seconds(game: Mapping[str, Any]) -> int:
    period = max(1, _integer(game.get("period"), 1))
    clock = max(0, _integer(game.get("clock_seconds"), 0))
    if period <= 4:
        return min(2400, (period - 1) * 600 + (600 - clock))
    return 2400 + (period - 5) * 300 + (300 - clock)


def _remaining_to_period_clock(
    remaining_minutes: float,
) -> tuple[int, int]:
    seconds = max(0, int(round(remaining_minutes * 60)))
    if seconds <= 600:
        return 4, seconds
    if seconds <= 1200:
        return 3, seconds - 600
    if seconds <= 1800:
        return 2, seconds - 1200
    return 1, min(600, seconds - 1800)


def _bayes_rate(
    success: float,
    attempts: float,
    prior_mean: float,
    prior_strength: float,
) -> float:
    return (
        success + prior_mean * prior_strength
    ) / max(attempts + prior_strength, 1e-9)


def _stat_row_value(
    row: Mapping[str, Any],
    *keys: str,
) -> float:
    for key in keys:
        if row.get(key) is not None:
            return _number(row.get(key))
    return 0.0


def _build_state_and_profiles(
    game: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    remaining_minutes: float | None,
) -> tuple[
    GameState,
    dict[str, PlayerRotationProfile],
    dict[str, PlayerEventProfile],
    dict[str, TeamSimulationProfile],
    dict[str, str],
]:
    home_team = str(game.get("home_team") or "")
    away_team = str(game.get("away_team") or "")
    if not home_team or not away_team:
        raise LiveReferenceError("Live game teams are unavailable.")

    config = GameConfig(
        game_id=str(game.get("canonical_game_id") or "live-game"),
        home_team=home_team,
        away_team=away_team,
    )

    period = max(1, _integer(game.get("period"), 1))
    clock_seconds = max(0, _integer(game.get("clock_seconds"), 0))
    if remaining_minutes is not None:
        period, clock_seconds = _remaining_to_period_clock(
            remaining_minutes
        )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    state = GameState(
        config=config,
        sequence=max(0, _integer(game.get("event_sequence"), 0)),
        period=period,
        clock_seconds=clock_seconds,
        home_score=max(0, _integer(game.get("home_score"), 0)),
        away_score=max(0, _integer(game.get("away_score"), 0)),
        possession_team=None,
        status=str(game.get("status") or "in"),
        last_source_timestamp_ms=now_ms,
        last_received_timestamp_ms=now_ms,
    )

    accepted_rows = [
        row
        for row in rows
        if _team_name(row) in {home_team, away_team}
        and _player_name(row)
    ]
    if not accepted_rows:
        raise LiveReferenceError(
            "The live player-stat snapshot did not contain either team."
        )

    name_to_id: dict[str, str] = {}
    row_by_id: dict[str, Mapping[str, Any]] = {}
    minutes_by_id: dict[str, float] = {}
    team_player_ids: dict[str, list[str]] = {
        home_team: [],
        away_team: [],
    }

    for row in accepted_rows:
        player_id = _player_id(row)
        player_name = _player_name(row)
        team_id = _team_name(row)
        minutes = _parse_minutes(row.get("min"))
        name_to_id[_normalized_name(player_name)] = player_id
        row_by_id[player_id] = row
        minutes_by_id[player_id] = minutes
        team_player_ids[team_id].append(player_id)

        state.players[player_id] = PlayerState(
            player_id=player_id,
            team_id=team_id,
            active=True,
            on_court=False,
            injury_state="healthy",
            current_stint_seconds=0,
            stats=PlayerStats(
                points=_integer(row.get("pts")),
                rebounds=_integer(
                    row.get("reb"),
                    _integer(row.get("oreb"))
                    + _integer(row.get("dreb")),
                ),
                assists=_integer(row.get("ast")),
                threes=_integer(row.get("fg3m")),
                steals=_integer(row.get("stl")),
                blocks=_integer(row.get("blk")),
                turnovers=_integer(
                    row.get("turnover"),
                    _integer(row.get("tov")),
                ),
                fouls=_integer(row.get("pf")),
                minutes_seconds=int(round(minutes * 60)),
            ),
        )

    for team_id, player_ids in team_player_ids.items():
        if len(player_ids) < 5:
            raise LiveReferenceError(
                f"Only {len(player_ids)} active players were found for "
                f"{team_id}; five are required."
            )

    home_lineup = tuple(
        sorted(
            team_player_ids[home_team],
            key=lambda player_id: minutes_by_id[player_id],
            reverse=True,
        )[:5]
    )
    away_lineup = tuple(
        sorted(
            team_player_ids[away_team],
            key=lambda player_id: minutes_by_id[player_id],
            reverse=True,
        )[:5]
    )
    state.home_lineup = home_lineup
    state.away_lineup = away_lineup
    for player_id in home_lineup + away_lineup:
        state.players[player_id].on_court = True

    elapsed_seconds = max(60, _elapsed_seconds(game))
    if remaining_minutes is not None:
        elapsed_seconds = max(
            60,
            2400 - int(round(remaining_minutes * 60)),
        )
    elapsed_minutes = elapsed_seconds / 60.0

    team_summaries: dict[str, dict[str, float]] = {}
    for team_id, player_ids in team_player_ids.items():
        summary = {
            "fga": 0.0,
            "fgm": 0.0,
            "fg3a": 0.0,
            "fg3m": 0.0,
            "fta": 0.0,
            "ftm": 0.0,
            "oreb": 0.0,
            "dreb": 0.0,
            "ast": 0.0,
            "tov": 0.0,
        }
        for player_id in player_ids:
            row = row_by_id[player_id]
            for key in tuple(summary):
                summary[key] += _stat_row_value(
                    row,
                    key,
                    "turnover" if key == "tov" else key,
                )
        team_summaries[team_id] = summary

    raw_paces: list[float] = []
    for team_id in (home_team, away_team):
        summary = team_summaries[team_id]
        possessions = (
            summary["fga"]
            + 0.44 * summary["fta"]
            - summary["oreb"]
            + summary["tov"]
        )
        raw_paces.append(
            possessions * 2400.0 / elapsed_seconds
        )
    observed_pace = sum(raw_paces) / max(len(raw_paces), 1)
    pace_weight = _clip(elapsed_seconds / 1200.0, 0.10, 0.85)
    pace_per_40 = _clip(
        78.0 * (1 - pace_weight) + observed_pace * pace_weight,
        68.0,
        92.0,
    )

    rotation_profiles: dict[str, PlayerRotationProfile] = {}
    player_profiles: dict[str, PlayerEventProfile] = {}

    for team_id, player_ids in team_player_ids.items():
        ranked = sorted(
            player_ids,
            key=lambda player_id: minutes_by_id[player_id],
            reverse=True,
        )

        usage_rates: dict[str, float] = {}
        assist_rates: dict[str, float] = {}
        oreb_rates: dict[str, float] = {}
        dreb_rates: dict[str, float] = {}

        for player_id in player_ids:
            row = row_by_id[player_id]
            minutes = minutes_by_id[player_id]
            fga = _stat_row_value(row, "fga")
            fta = _stat_row_value(row, "fta")
            tov = _stat_row_value(row, "turnover", "tov")
            usage_rates[player_id] = (
                fga + 0.44 * fta + tov + 2.5
            ) / (minutes + 6.0)
            assist_rates[player_id] = (
                _stat_row_value(row, "ast") + 1.0
            ) / (minutes + 6.0)
            oreb_rates[player_id] = (
                _stat_row_value(row, "oreb") + 0.8
            ) / (minutes + 6.0)
            dreb_rates[player_id] = (
                _stat_row_value(row, "dreb") + 1.8
            ) / (minutes + 6.0)

        mean_usage = sum(usage_rates.values()) / len(usage_rates)
        mean_assist = sum(assist_rates.values()) / len(assist_rates)
        mean_oreb = sum(oreb_rates.values()) / len(oreb_rates)
        mean_dreb = sum(dreb_rates.values()) / len(dreb_rates)

        for rank, player_id in enumerate(ranked):
            row = row_by_id[player_id]
            minutes = minutes_by_id[player_id]
            role_target = (
                32.0
                if rank < 5
                else 20.0
                if rank < 8
                else 10.0
            )
            projected = _clip(
                minutes * 40.0 / max(elapsed_minutes, 1.0),
                4.0,
                38.0,
            )
            target_total = max(
                minutes,
                0.65 * role_target + 0.35 * projected,
            )
            rotation_profiles[player_id] = PlayerRotationProfile(
                player_id=player_id,
                target_total_minutes=_clip(
                    target_total,
                    minutes,
                    40.0,
                ),
                rotation_weight=1.0,
                closing_priority=1.0 if rank < 5 else 0.25,
                foul_sensitivity=1.0,
                blowout_sensitivity=1.0,
            )

            fga = _stat_row_value(row, "fga")
            fgm = _stat_row_value(row, "fgm")
            fg3a = _stat_row_value(row, "fg3a")
            fg3m = _stat_row_value(row, "fg3m")
            fta = _stat_row_value(row, "fta")
            ftm = _stat_row_value(row, "ftm")
            tov = _stat_row_value(row, "turnover", "tov")
            possessions_used = max(
                1.0,
                fga + 0.44 * fta + tov,
            )
            two_attempts = max(0.0, fga - fg3a)
            two_makes = max(0.0, fgm - fg3m)

            player_profiles[player_id] = PlayerEventProfile(
                player_id=player_id,
                usage_weight=_clip(
                    usage_rates[player_id] / max(mean_usage, 1e-9),
                    0.45,
                    2.40,
                ),
                three_point_share=_clip(
                    _bayes_rate(fg3a, fga, 0.34, 8.0),
                    0.05,
                    0.75,
                ),
                two_point_pct=_clip(
                    _bayes_rate(
                        two_makes,
                        two_attempts,
                        0.50,
                        10.0,
                    ),
                    0.30,
                    0.72,
                ),
                three_point_pct=_clip(
                    _bayes_rate(fg3m, fg3a, 0.34, 10.0),
                    0.18,
                    0.58,
                ),
                free_throw_pct=_clip(
                    _bayes_rate(ftm, fta, 0.80, 10.0),
                    0.45,
                    0.96,
                ),
                turnover_probability=_clip(
                    _bayes_rate(tov, possessions_used, 0.12, 12.0),
                    0.04,
                    0.28,
                ),
                shooting_foul_probability=_clip(
                    _bayes_rate(
                        fta / 2.0,
                        possessions_used,
                        0.12,
                        12.0,
                    ),
                    0.04,
                    0.25,
                ),
                assist_weight=_clip(
                    assist_rates[player_id] / max(mean_assist, 1e-9),
                    0.35,
                    3.00,
                ),
                offensive_rebound_weight=_clip(
                    oreb_rates[player_id] / max(mean_oreb, 1e-9),
                    0.30,
                    3.50,
                ),
                defensive_rebound_weight=_clip(
                    dreb_rates[player_id] / max(mean_dreb, 1e-9),
                    0.30,
                    3.50,
                ),
                steal_probability=_clip(
                    (
                        _stat_row_value(row, "stl") + 0.3
                    ) / (minutes + 15.0) * 0.45,
                    0.004,
                    0.06,
                ),
                block_probability=_clip(
                    (
                        _stat_row_value(row, "blk") + 0.2
                    ) / (minutes + 15.0) * 0.35,
                    0.002,
                    0.08,
                ),
            )

    team_profiles: dict[str, TeamSimulationProfile] = {}
    for team_id, opponent in (
        (home_team, away_team),
        (away_team, home_team),
    ):
        own = team_summaries[team_id]
        other = team_summaries[opponent]
        oreb_probability = _bayes_rate(
            own["oreb"],
            own["oreb"] + other["dreb"],
            0.25,
            18.0,
        )
        assisted_probability = _bayes_rate(
            own["ast"],
            own["fgm"],
            0.62,
            18.0,
        )
        team_profiles[team_id] = TeamSimulationProfile(
            team_id=team_id,
            pace_per_40=pace_per_40,
            offensive_rebound_probability=_clip(
                oreb_probability,
                0.12,
                0.42,
            ),
            assisted_make_probability=_clip(
                assisted_probability,
                0.40,
                0.82,
            ),
        )

    return (
        state,
        rotation_profiles,
        player_profiles,
        team_profiles,
        name_to_id,
    )


def _market_spec(
    market: Mapping[str, Any],
    *,
    name_to_id: Mapping[str, str],
    custom_line: float | None,
    custom_side: str | None,
    custom_american_odds: int | None,
) -> tuple[MarketSpec, int]:
    market_key = str(market.get("market_key") or "")
    side = str(custom_side or market.get("side") or "").lower()
    point = market.get("line")
    line = (
        float(custom_line)
        if custom_line is not None
        else _number(point, 0.5 if market_key == "h2h" else 0.0)
    )
    american_odds = int(
        custom_american_odds
        if custom_american_odds is not None
        else market.get("american_odds")
    )
    reference_decimal = american_to_decimal(american_odds)
    market_id = str(
        market.get("market_id")
        or market.get("recommendation_id")
        or "live-market"
    )

    if market_key in PLAYER_MARKET_STATS:
        player_name = str(
            market.get("player_name")
            or market.get("selection")
            or ""
        )
        player_id = name_to_id.get(_normalized_name(player_name))
        if player_id is None:
            raise LiveReferenceError(
                f"Player '{player_name}' was not found in the live box score."
            )
        if side not in {"over", "under"}:
            raise LiveReferenceError(
                "Player props require an over or under side."
            )
        return (
            MarketSpec(
                market_id=market_id,
                market_type="player_prop",
                player_id=player_id,
                stats=PLAYER_MARKET_STATS[market_key],
                line=line,
                side=side,
                reference_decimal_odds=reference_decimal,
            ),
            american_odds,
        )

    home_team = str(market.get("home_team") or "")
    selection = str(market.get("selection") or "")

    if market_key == "totals":
        if side not in {"over", "under"}:
            raise LiveReferenceError(
                "Game totals require an over or under side."
            )
        return (
            MarketSpec(
                market_id=market_id,
                market_type="game_total",
                line=line,
                side=side,
                reference_decimal_odds=reference_decimal,
            ),
            american_odds,
        )

    if market_key == "spreads":
        is_home = _normalized_name(selection) == _normalized_name(
            home_team
        )
        if custom_line is None:
            line = -line if is_home else line
        mapped_side = (
            side
            if custom_side in {"over", "under"}
            else "over" if is_home else "under"
        )
        return (
            MarketSpec(
                market_id=market_id,
                market_type="home_spread",
                line=line,
                side=mapped_side,
                reference_decimal_odds=reference_decimal,
            ),
            american_odds,
        )

    if market_key == "h2h":
        is_home = _normalized_name(selection) == _normalized_name(
            home_team
        )
        mapped_side = "over" if is_home else "under"
        return (
            MarketSpec(
                market_id=market_id,
                market_type="moneyline_home",
                line=0.5,
                side=mapped_side,
                reference_decimal_odds=reference_decimal,
            ),
            american_odds,
        )

    raise LiveReferenceError(
        f"Unsupported live market: {market_key}"
    )


def _apply_current_total(
    state: GameState,
    spec: MarketSpec,
    current_total: float | None,
) -> None:
    if current_total is None or spec.market_type != "player_prop":
        return
    assert spec.player_id is not None
    player = state.players.get(spec.player_id)
    if player is None:
        raise LiveReferenceError(
            "The selected player is not in the simulation state."
        )
    target = max(0, int(round(current_total)))
    if len(spec.stats) == 1:
        setattr(player.stats, spec.stats[0], target)
        return

    current = sum(
        int(getattr(player.stats, stat))
        for stat in spec.stats
    )
    player.stats.points = max(
        0,
        player.stats.points + target - current,
    )


def _decimal_to_american(decimal_odds: float) -> int | None:
    if not math.isfinite(decimal_odds) or decimal_odds <= 1:
        return None
    if decimal_odds >= 2:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


def simulate_live_reference(
    *,
    data_dir: Path,
    snapshot: Mapping[str, Any],
    market_feed: Mapping[str, Any],
    market_id: str,
    side: str | None = None,
    line: float | None = None,
    american_odds: int | None = None,
    current_total: float | None = None,
    remaining_minutes: float | None = None,
    simulations: int = 20_000,
    seed: int = 20260714,
) -> dict[str, Any]:
    if simulations < 1000 or simulations > 100_000:
        raise LiveReferenceError(
            "Simulations must be between 1,000 and 100,000."
        )

    market = next(
        (
            item
            for item in market_feed.get("markets", [])
            if str(item.get("market_id")) == market_id
        ),
        None,
    )
    if market is None:
        raise LiveReferenceError(
            "The selected live sportsbook line is no longer available."
        )

    canonical_game_id = str(
        market.get("canonical_game_id") or ""
    )
    game = next(
        (
            item
            for item in snapshot.get("games", [])
            if str(item.get("canonical_game_id"))
            == canonical_game_id
        ),
        None,
    )
    if game is None:
        raise LiveReferenceError(
            "The live game state is not present in the current snapshot."
        )

    source_game_id = canonical_game_id.removeprefix("bdl-")
    rows = _latest_player_rows(data_dir, source_game_id)
    (
        state,
        rotations,
        players,
        teams,
        name_to_id,
    ) = _build_state_and_profiles(
        game,
        rows,
        remaining_minutes=remaining_minutes,
    )

    spec, offered_american = _market_spec(
        market,
        name_to_id=name_to_id,
        custom_line=line,
        custom_side=side,
        custom_american_odds=american_odds,
    )
    _apply_current_total(state, spec, current_total)

    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    run_id = (
        f"live-reference-{canonical_game_id}-"
        f"{state.sequence}-{now_ms}"
    )
    profile_uncertainty = _clip(
        0.020
        + 0.018 * (1 - min(_elapsed_seconds(game) / 2400.0, 1))
        + 0.008 * (1 if spec.market_type == "player_prop" else 0),
        0.020,
        0.055,
    )

    quote_context = QuoteContext(
        base_margin=0.0,
        base_limit=0.0,
        exposure_over=0.0,
        exposure_under=0.0,
        risk_capacity=1.0,
        input_age_ms=max(
            0,
            int(_number(game.get("state_age_seconds")) * 1000),
        ),
        max_input_age_ms=30_000,
        model_uncertainty=profile_uncertainty,
        max_model_uncertainty=0.12,
        monte_carlo_error=0.0,
        max_monte_carlo_error=0.03,
        calibration=CalibrationStatus(
            applied=False,
            calibrator_id=None,
            reason="LIVE_REFERENCE_ENGINE",
        ),
        require_calibration=False,
    )
    metadata = RunMetadata(
        run_id=run_id,
        commit_sha="integrated-live-reference",
        model_version="possession-reference-v1",
        calibration_version="not-yet-oos-calibrated",
        event_sequence=state.sequence,
        as_of_timestamp_ms=now_ms,
        source_timestamp_ms=min(
            now_ms,
            max(0, state.last_source_timestamp_ms),
        ),
        input_fingerprint=fingerprint(state.to_dict()),
    )
    report = SimulationService().run(
        SimulationRequest(
            state=state,
            rotation_profiles=rotations,
            player_profiles=players,
            team_profiles=teams,
            markets=(spec,),
            quote_contexts={spec.market_id: quote_context},
            metadata=metadata,
            simulations=simulations,
            seed=seed + state.sequence,
        )
    )
    output = report.markets[0]

    if spec.side == "over":
        p_win = output.p_over
        p_loss = output.p_under
        fair_decimal = output.fair_decimal_over
    else:
        p_win = output.p_under
        p_loss = output.p_over
        fair_decimal = output.fair_decimal_under

    offered_decimal = american_to_decimal(offered_american)
    expected_roi = p_win * (offered_decimal - 1) - p_loss
    total_uncertainty = math.sqrt(
        output.monte_carlo_error**2
        + profile_uncertainty**2
    )
    allowance = 1.645 * total_uncertainty
    conservative_win = max(0.0, p_win - allowance)
    conservative_loss = min(
        1.0 - output.p_push,
        p_loss + allowance,
    )
    conservative_roi = (
        conservative_win * (offered_decimal - 1)
        - conservative_loss
    )

    return {
        "run_id": run_id,
        "market_id": market_id,
        "market_key": market.get("market_key"),
        "selection": market.get("selection"),
        "bookmaker_key": market.get("bookmaker_key"),
        "bookmaker_title": market.get("bookmaker_title"),
        "side": spec.side,
        "line": spec.line,
        "american_odds": offered_american,
        "win_probability": p_win,
        "push_probability": output.p_push,
        "loss_probability": p_loss,
        "fair_american_odds": _decimal_to_american(fair_decimal),
        "expected_roi": expected_roi,
        "conservative_roi": conservative_roi,
        "projected_mean": output.projected_mean,
        "projected_variance": output.projected_variance,
        "monte_carlo_error": output.monte_carlo_error,
        "profile_uncertainty": profile_uncertainty,
        "total_uncertainty": total_uncertainty,
        "simulation_count": simulations,
        "pmf": {
            str(value): probability
            for value, probability in output.pmf.items()
        },
        "manifest": report.manifest.to_dict(),
        "engine": "wnba_inplay.InPlaySimulator",
        "engine_status": "LIVE_REFERENCE_ACTIVE",
        "calibration_status": "NOT_OOS_CALIBRATED",
        "official_recommendation": False,
        "note": (
            "This result uses the integrated possession-based reference "
            "simulator with live game state and Bayesian-shrunk live profiles. "
            "It is not an official published recommendation until WNBA-specific "
            "out-of-sample calibrators are attached."
        ),
    }
