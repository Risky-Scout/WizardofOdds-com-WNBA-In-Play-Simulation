from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import random
from statistics import fmean, pstdev
from typing import Mapping, Sequence

from .domain import (
    GameState,
    PlayerLiveState,
    PlayerRateProfile,
    ProbabilityEstimate,
)
from .odds_math import fair_decimal_with_push


STAT_FIELDS = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes",
}


@dataclass(frozen=True)
class SimulationBundle:
    player_final_stats: tuple[Mapping[str, Mapping[str, int]], ...]
    home_final_scores: tuple[int, ...]
    away_final_scores: tuple[int, ...]


class MonteCarloEngine:
    """Transparent correlated reference engine.

    Production deployments should replace rate and minutes profile estimates
    with fitted, time-split WNBA models while retaining these interfaces.
    """

    def simulate(
        self,
        *,
        game: GameState,
        live_players: Mapping[str, PlayerLiveState],
        profiles: Mapping[str, PlayerRateProfile],
        simulations: int,
        seed: int,
    ) -> SimulationBundle:
        if simulations <= 0:
            raise ValueError("simulations must be positive")
        rng = random.Random(seed)
        player_paths: list[dict[str, dict[str, int]]] = []
        home_scores: list[int] = []
        away_scores: list[int] = []

        for _ in range(simulations):
            pace = max(0.80, min(1.22, rng.gauss(1.0, 0.06)))
            shooting_environment = rng.gauss(0.0, 0.08)
            foul_environment = max(0.75, min(1.30, rng.gauss(1.0, 0.08)))
            path: dict[str, dict[str, int]] = {}

            home_added = 0
            away_added = 0
            for player_id, live in live_players.items():
                profile = profiles[player_id]
                remaining_minutes = max(
                    0.0,
                    rng.gauss(
                        profile.expected_remaining_minutes,
                        profile.remaining_minutes_sd,
                    ),
                )
                rate_multiplier = (
                    profile.usage_multiplier
                    * profile.pace_multiplier
                    * pace
                    * math.exp(shooting_environment * 0.25)
                )

                means = {
                    "points": (
                        profile.points_per_minute
                        * remaining_minutes
                        * rate_multiplier
                        * foul_environment**0.15
                    ),
                    "rebounds": (
                        profile.rebounds_per_minute
                        * remaining_minutes
                        * pace
                    ),
                    "assists": (
                        profile.assists_per_minute
                        * remaining_minutes
                        * rate_multiplier**0.55
                    ),
                    "threes": (
                        profile.threes_per_minute
                        * remaining_minutes
                        * rate_multiplier
                    ),
                }
                final = {
                    "points": live.points + self._poisson(means["points"], rng),
                    "rebounds": live.rebounds + self._poisson(means["rebounds"], rng),
                    "assists": live.assists + self._poisson(means["assists"], rng),
                    "threes": live.threes + self._poisson(means["threes"], rng),
                }
                path[player_id] = final
                added = final["points"] - live.points
                if live.team == game.home_team:
                    home_added += added
                elif live.team == game.away_team:
                    away_added += added

            # Residual scoring accounts for unmapped players and team events.
            seconds_ratio = game.regulation_seconds_remaining / 2400
            residual_home = self._poisson(max(0.0, 30 * seconds_ratio * pace), rng)
            residual_away = self._poisson(max(0.0, 30 * seconds_ratio * pace), rng)
            home_score = game.home_score + home_added + residual_home
            away_score = game.away_score + away_added + residual_away
            if home_score == away_score:
                if rng.random() < 0.5:
                    home_score += self._poisson(6.0, rng) + 1
                else:
                    away_score += self._poisson(6.0, rng) + 1

            player_paths.append(path)
            home_scores.append(home_score)
            away_scores.append(away_score)

        return SimulationBundle(
            player_final_stats=tuple(player_paths),
            home_final_scores=tuple(home_scores),
            away_final_scores=tuple(away_scores),
        )

    def price_player_market(
        self,
        *,
        bundle: SimulationBundle,
        player_id: str,
        market_key: str,
        line: float,
        side: str,
        calibration_score: float,
        calibrator_id: str,
        model_version: str,
        calibration_se: float,
        model_se: float,
    ) -> ProbabilityEstimate:
        values = self._extract_player_values(bundle, player_id, market_key)
        return self._estimate(
            values=values,
            line=line,
            side=side,
            market_key=market_key,
            calibration_score=calibration_score,
            calibrator_id=calibrator_id,
            model_version=model_version,
            calibration_se=calibration_se,
            model_se=model_se,
        )

    def price_game_market(
        self,
        *,
        bundle: SimulationBundle,
        market_key: str,
        line: float,
        side: str,
        calibration_score: float,
        calibrator_id: str,
        model_version: str,
        calibration_se: float,
        model_se: float,
    ) -> ProbabilityEstimate:
        if market_key == "totals":
            values = [
                home + away
                for home, away in zip(
                    bundle.home_final_scores,
                    bundle.away_final_scores,
                    strict=True,
                )
            ]
        elif market_key == "spreads":
            values = [
                home - away
                for home, away in zip(
                    bundle.home_final_scores,
                    bundle.away_final_scores,
                    strict=True,
                )
            ]
        elif market_key == "h2h":
            values = [
                1 if home > away else 0
                for home, away in zip(
                    bundle.home_final_scores,
                    bundle.away_final_scores,
                    strict=True,
                )
            ]
        else:
            raise ValueError(f"unsupported game market: {market_key}")
        return self._estimate(
            values=values,
            line=line,
            side=side,
            market_key=market_key,
            calibration_score=calibration_score,
            calibrator_id=calibrator_id,
            model_version=model_version,
            calibration_se=calibration_se,
            model_se=model_se,
        )

    @staticmethod
    def _extract_player_values(
        bundle: SimulationBundle,
        player_id: str,
        market_key: str,
    ) -> list[int]:
        if market_key in STAT_FIELDS:
            field = STAT_FIELDS[market_key]
            return [path[player_id][field] for path in bundle.player_final_stats]
        if market_key == "player_points_rebounds_assists":
            return [
                path[player_id]["points"]
                + path[player_id]["rebounds"]
                + path[player_id]["assists"]
                for path in bundle.player_final_stats
            ]
        raise ValueError(f"unsupported player market: {market_key}")

    @staticmethod
    def _estimate(
        *,
        values: Sequence[int],
        line: float,
        side: str,
        market_key: str,
        calibration_score: float,
        calibrator_id: str,
        model_version: str,
        calibration_se: float,
        model_se: float,
    ) -> ProbabilityEstimate:
        if not values:
            raise ValueError("values cannot be empty")
        if side.lower() not in {"over", "under"}:
            raise ValueError("side must be over or under")

        wins = sum(
            value > line if side.lower() == "over" else value < line
            for value in values
        )
        pushes = sum(math.isclose(value, line) for value in values)
        losses = len(values) - wins - pushes
        p_win = wins / len(values)
        p_push = pushes / len(values)
        p_loss = losses / len(values)
        mc_se = math.sqrt(max(p_win * (1 - p_win), 0.0) / len(values))
        pmf_counts = Counter(values)
        pmf = {
            key: count / len(values)
            for key, count in sorted(pmf_counts.items())
        }
        estimate = ProbabilityEstimate(
            market_key=market_key,
            line=line,
            p_win=p_win,
            p_push=p_push,
            p_loss=p_loss,
            fair_decimal_odds=fair_decimal_with_push(p_win, p_push),
            projected_mean=fmean(values),
            projected_sd=pstdev(values) if len(values) > 1 else 0.0,
            simulation_count=len(values),
            monte_carlo_se=mc_se,
            calibration_se=calibration_se,
            model_se=model_se,
            calibration_score=calibration_score,
            calibrator_id=calibrator_id,
            model_version=model_version,
            pmf=pmf,
        )
        estimate.validate()
        return estimate

    @staticmethod
    def _poisson(rate: float, rng: random.Random) -> int:
        if rate <= 0:
            return 0
        if rate > 30:
            return max(0, round(rng.gauss(rate, math.sqrt(rate))))
        threshold = math.exp(-rate)
        product = 1.0
        count = 0
        while product > threshold:
            product *= rng.random()
            count += 1
        return count - 1
