from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
import random
from statistics import fmean
from typing import Callable, Mapping, Sequence

from .domain import GameState, PlayerState, PlayerStats
from .rotation import (
    PlayerRotationProfile,
    RotationAllocator,
    RotationContext,
    game_seconds_remaining,
)


@dataclass(frozen=True)
class PlayerEventProfile:
    player_id: str
    usage_weight: float
    three_point_share: float
    two_point_pct: float
    three_point_pct: float
    free_throw_pct: float
    turnover_probability: float
    shooting_foul_probability: float
    assist_weight: float
    offensive_rebound_weight: float
    defensive_rebound_weight: float
    steal_probability: float
    block_probability: float

    def validate(self) -> None:
        positive_fields = {
            "usage_weight": self.usage_weight,
            "assist_weight": self.assist_weight,
            "offensive_rebound_weight": self.offensive_rebound_weight,
            "defensive_rebound_weight": self.defensive_rebound_weight,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        probability_fields = {
            "three_point_share": self.three_point_share,
            "two_point_pct": self.two_point_pct,
            "three_point_pct": self.three_point_pct,
            "free_throw_pct": self.free_throw_pct,
            "turnover_probability": self.turnover_probability,
            "shooting_foul_probability": self.shooting_foul_probability,
            "steal_probability": self.steal_probability,
            "block_probability": self.block_probability,
        }
        for name, value in probability_fields.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True)
class TeamSimulationProfile:
    team_id: str
    pace_per_40: float = 78.0
    offensive_rebound_probability: float = 0.25
    assisted_make_probability: float = 0.62

    def validate(self) -> None:
        if self.pace_per_40 <= 0:
            raise ValueError("pace_per_40 must be positive")
        if not 0 <= self.offensive_rebound_probability <= 1:
            raise ValueError("offensive_rebound_probability must be in [0, 1]")
        if not 0 <= self.assisted_make_probability <= 1:
            raise ValueError("assisted_make_probability must be in [0, 1]")


@dataclass(frozen=True)
class LatentFactors:
    pace_multiplier: float
    home_shooting_logit_shift: float
    away_shooting_logit_shift: float
    foul_multiplier: float
    offensive_rebound_logit_shift: float


@dataclass(frozen=True)
class SimulationPath:
    home_score: int
    away_score: int
    player_stats: Mapping[str, PlayerStats]
    overtime_periods: int
    possessions: int

    @property
    def home_margin(self) -> int:
        return self.home_score - self.away_score

    @property
    def game_total(self) -> int:
        return self.home_score + self.away_score


@dataclass(frozen=True)
class OverPushUnder:
    line: float
    p_over: float
    p_push: float
    p_under: float

    def validate(self) -> None:
        values = (self.p_over, self.p_push, self.p_under)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("probabilities must be in [0, 1]")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-12):
            raise ValueError("over/push/under probabilities must sum to one")


@dataclass(frozen=True)
class DiscretePMF:
    probabilities: Mapping[int, float]

    @classmethod
    def from_samples(cls, values: Sequence[int]) -> "DiscretePMF":
        if not values:
            raise ValueError("values cannot be empty")
        counts = Counter(int(value) for value in values)
        total = sum(counts.values())
        pmf = cls(
            probabilities={
                value: count / total
                for value, count in sorted(counts.items())
            }
        )
        pmf.validate()
        return pmf

    def validate(self) -> None:
        if not self.probabilities:
            raise ValueError("PMF cannot be empty")
        if any(
            not math.isfinite(probability) or probability < 0
            for probability in self.probabilities.values()
        ):
            raise ValueError("PMF contains invalid probability")
        if not math.isclose(
            sum(self.probabilities.values()),
            1.0,
            abs_tol=1e-12,
        ):
            raise ValueError("PMF is not normalized")

    def mean(self) -> float:
        return sum(
            value * probability
            for value, probability in self.probabilities.items()
        )

    def variance(self) -> float:
        mean = self.mean()
        return sum(
            (value - mean) ** 2 * probability
            for value, probability in self.probabilities.items()
        )

    def over_push_under(self, line: float) -> OverPushUnder:
        result = OverPushUnder(
            line=line,
            p_over=sum(
                probability
                for value, probability in self.probabilities.items()
                if value > line
            ),
            p_push=sum(
                probability
                for value, probability in self.probabilities.items()
                if math.isclose(value, line)
            ),
            p_under=sum(
                probability
                for value, probability in self.probabilities.items()
                if value < line
            ),
        )
        result.validate()
        return result


class InPlaySimulator:
    """Correlated possession-based rest-of-game Monte Carlo simulator."""

    def __init__(
        self,
        rotation_allocator: RotationAllocator | None = None,
    ) -> None:
        self.rotation_allocator = rotation_allocator or RotationAllocator()

    def simulate(
        self,
        state: GameState,
        rotation_profiles: Mapping[str, PlayerRotationProfile],
        player_profiles: Mapping[str, PlayerEventProfile],
        team_profiles: Mapping[str, TeamSimulationProfile],
        simulations: int,
        seed: int,
    ) -> list[SimulationPath]:
        if simulations <= 0:
            raise ValueError("simulations must be positive")
        self._validate_inputs(
            state,
            rotation_profiles,
            player_profiles,
            team_profiles,
        )

        master_rng = random.Random(seed)
        seeds = [master_rng.getrandbits(64) for _ in range(simulations)]
        return [
            self._simulate_one(
                state,
                rotation_profiles,
                player_profiles,
                team_profiles,
                random.Random(path_seed),
            )
            for path_seed in seeds
        ]

    def _simulate_one(
        self,
        original_state: GameState,
        rotation_profiles: Mapping[str, PlayerRotationProfile],
        player_profiles: Mapping[str, PlayerEventProfile],
        team_profiles: Mapping[str, TeamSimulationProfile],
        rng: random.Random,
    ) -> SimulationPath:
        state = deepcopy(original_state)
        latent = self._sample_latent_factors(rng)
        remaining = game_seconds_remaining(state)

        allocations = self.rotation_allocator.sample(
            state,
            rotation_profiles,
            RotationContext(
                game_seconds_remaining=remaining,
                score_margin=state.home_score - state.away_score,
                overtime_probability=0.0,
            ),
            simulations=1,
            seed=rng.getrandbits(64),
        )[0]

        possessions = 0
        possession_team = (
            state.possession_team
            or state.config.home_team
        )
        overtime_periods = 0

        while True:
            if remaining <= 0:
                if state.home_score != state.away_score:
                    break
                overtime_periods += 1
                if overtime_periods > 8:
                    # Deterministic tie-break possession prevents an unbounded loop
                    # in pathological low-scoring parameterizations.
                    if rng.random() < 0.5:
                        state.home_score += 1
                    else:
                        state.away_score += 1
                    break
                remaining = state.config.overtime_period_seconds
                allocations = self.rotation_allocator.sample(
                    state,
                    rotation_profiles,
                    RotationContext(
                        game_seconds_remaining=remaining,
                        score_margin=0,
                        overtime_probability=0.0,
                    ),
                    simulations=1,
                    seed=rng.getrandbits(64),
                )[0]

            offense = possession_team
            defense = (
                state.config.away_team
                if offense == state.config.home_team
                else state.config.home_team
            )
            offense_lineup = self._select_lineup(
                state,
                offense,
                allocations,
                rotation_profiles,
            )
            defense_lineup = self._select_lineup(
                state,
                defense,
                allocations,
                rotation_profiles,
            )

            elapsed = self._sample_possession_seconds(
                team_profiles[offense],
                latent,
                remaining,
                state.home_score - state.away_score,
                rng,
            )
            elapsed = max(1, min(elapsed, remaining))

            self._credit_minutes(
                state,
                offense_lineup + defense_lineup,
                elapsed,
                allocations,
            )
            possession_team = self._simulate_possession(
                state=state,
                offense=offense,
                defense=defense,
                offense_lineup=offense_lineup,
                defense_lineup=defense_lineup,
                player_profiles=player_profiles,
                team_profiles=team_profiles,
                latent=latent,
                rng=rng,
            )
            remaining -= elapsed
            possessions += 1

            if possessions > 1000:
                raise RuntimeError("simulation exceeded possession safety limit")

        return SimulationPath(
            home_score=state.home_score,
            away_score=state.away_score,
            player_stats={
                player_id: deepcopy(player.stats)
                for player_id, player in state.players.items()
            },
            overtime_periods=overtime_periods,
            possessions=possessions,
        )

    @staticmethod
    def _sample_latent_factors(rng: random.Random) -> LatentFactors:
        return LatentFactors(
            pace_multiplier=max(0.78, min(1.24, rng.gauss(1.0, 0.055))),
            home_shooting_logit_shift=rng.gauss(0.0, 0.11),
            away_shooting_logit_shift=rng.gauss(0.0, 0.11),
            foul_multiplier=max(0.70, min(1.35, rng.gauss(1.0, 0.10))),
            offensive_rebound_logit_shift=rng.gauss(0.0, 0.14),
        )

    @staticmethod
    def _sample_possession_seconds(
        team: TeamSimulationProfile,
        latent: LatentFactors,
        remaining: int,
        score_margin: int,
        rng: random.Random,
    ) -> int:
        baseline = 2400.0 / team.pace_per_40
        # pace_per_40 counts team possessions, while clock is shared by teams;
        # half the baseline approximates alternating-possession duration.
        mean_seconds = baseline / 2.0 / latent.pace_multiplier
        if remaining <= 120 and abs(score_margin) <= 8:
            mean_seconds *= 0.78
        shape = 5.0
        scale = max(mean_seconds / shape, 0.25)
        return max(1, round(rng.gammavariate(shape, scale)))

    def _simulate_possession(
        self,
        state: GameState,
        offense: str,
        defense: str,
        offense_lineup: tuple[str, ...],
        defense_lineup: tuple[str, ...],
        player_profiles: Mapping[str, PlayerEventProfile],
        team_profiles: Mapping[str, TeamSimulationProfile],
        latent: LatentFactors,
        rng: random.Random,
    ) -> str:
        shooter_id = self._weighted_choice(
            offense_lineup,
            [player_profiles[player_id].usage_weight for player_id in offense_lineup],
            rng,
        )
        shooter = player_profiles[shooter_id]

        turnover_p = min(max(shooter.turnover_probability, 0.01), 0.40)
        foul_p = min(
            shooter.shooting_foul_probability * latent.foul_multiplier,
            0.35,
        )
        draw = rng.random()

        if draw < turnover_p:
            state.players[shooter_id].stats.apply("turnovers", 1)
            possible_stealers = [
                player_id
                for player_id in defense_lineup
                if rng.random() < player_profiles[player_id].steal_probability
            ]
            if possible_stealers:
                stealer = rng.choice(possible_stealers)
                state.players[stealer].stats.apply("steals", 1)
            return defense

        if draw < turnover_p + foul_p:
            attempts = 3 if rng.random() < shooter.three_point_share * 0.25 else 2
            made = sum(
                1
                for _ in range(attempts)
                if rng.random() < shooter.free_throw_pct
            )
            if made:
                state.players[shooter_id].stats.apply("points", made)
                self._add_score(state, offense, made)
            return defense

        is_three = rng.random() < shooter.three_point_share
        base_make = shooter.three_point_pct if is_three else shooter.two_point_pct
        shift = (
            latent.home_shooting_logit_shift
            if offense == state.config.home_team
            else latent.away_shooting_logit_shift
        )

        blocked_by = None
        if not is_three:
            blockers = [
                player_id
                for player_id in defense_lineup
                if rng.random() < player_profiles[player_id].block_probability
            ]
            if blockers:
                blocked_by = rng.choice(blockers)

        make_probability = 0.0 if blocked_by else self._shift_probability(base_make, shift)
        made = rng.random() < make_probability

        if made:
            points = 3 if is_three else 2
            state.players[shooter_id].stats.apply("points", points)
            if is_three:
                state.players[shooter_id].stats.apply("threes", 1)
            self._add_score(state, offense, points)

            teammate_ids = [
                player_id for player_id in offense_lineup
                if player_id != shooter_id
            ]
            if (
                teammate_ids
                and rng.random()
                < team_profiles[offense].assisted_make_probability
            ):
                assister = self._weighted_choice(
                    teammate_ids,
                    [
                        player_profiles[player_id].assist_weight
                        for player_id in teammate_ids
                    ],
                    rng,
                )
                state.players[assister].stats.apply("assists", 1)
            return defense

        if blocked_by is not None:
            state.players[blocked_by].stats.apply("blocks", 1)

        offense_rebound_probability = self._shift_probability(
            team_profiles[offense].offensive_rebound_probability,
            latent.offensive_rebound_logit_shift,
        )
        if rng.random() < offense_rebound_probability:
            rebounder = self._weighted_choice(
                offense_lineup,
                [
                    player_profiles[player_id].offensive_rebound_weight
                    for player_id in offense_lineup
                ],
                rng,
            )
            state.players[rebounder].stats.apply("rebounds", 1)
            return offense

        rebounder = self._weighted_choice(
            defense_lineup,
            [
                player_profiles[player_id].defensive_rebound_weight
                for player_id in defense_lineup
            ],
            rng,
        )
        state.players[rebounder].stats.apply("rebounds", 1)
        return defense

    @staticmethod
    def _add_score(state: GameState, team_id: str, points: int) -> None:
        if team_id == state.config.home_team:
            state.home_score += points
        elif team_id == state.config.away_team:
            state.away_score += points
        else:
            raise ValueError("unknown team")

    @staticmethod
    def _credit_minutes(
        state: GameState,
        lineup: Sequence[str],
        elapsed: int,
        allocations: dict[str, int],
    ) -> None:
        for player_id in lineup:
            state.players[player_id].stats.apply("minutes_seconds", elapsed)
            allocations[player_id] = max(0, allocations[player_id] - elapsed)

    @staticmethod
    def _select_lineup(
        state: GameState,
        team_id: str,
        allocations: Mapping[str, int],
        profiles: Mapping[str, PlayerRotationProfile],
    ) -> tuple[str, ...]:
        current = (
            set(state.home_lineup)
            if team_id == state.config.home_team
            else set(state.away_lineup)
        )
        candidates = [
            player
            for player in state.players.values()
            if player.team_id == team_id
            and player.active
            and player.injury_state != "removed"
            and player.player_id in profiles
        ]
        if len(candidates) < 5:
            raise ValueError("cannot select a legal five-player lineup")
        ranked = sorted(
            candidates,
            key=lambda player: (
                allocations.get(player.player_id, 0)
                + (45 if player.player_id in current else 0)
                + 20 * profiles[player.player_id].closing_priority
            ),
            reverse=True,
        )
        return tuple(player.player_id for player in ranked[:5])

    @staticmethod
    def _weighted_choice(
        values: Sequence[str],
        weights: Sequence[float],
        rng: random.Random,
    ) -> str:
        if len(values) != len(weights) or not values:
            raise ValueError("values and weights must be non-empty and aligned")
        total = sum(max(weight, 0.0) for weight in weights)
        if total <= 0:
            raise ValueError("at least one weight must be positive")
        threshold = rng.random() * total
        cumulative = 0.0
        for value, weight in zip(values, weights, strict=True):
            cumulative += max(weight, 0.0)
            if threshold <= cumulative:
                return value
        return values[-1]

    @staticmethod
    def _shift_probability(probability: float, logit_shift: float) -> float:
        clipped = min(max(probability, 1e-8), 1 - 1e-8)
        logit = math.log(clipped / (1 - clipped)) + logit_shift
        return 1.0 / (1.0 + math.exp(-logit))

    @staticmethod
    def _validate_inputs(
        state: GameState,
        rotation_profiles: Mapping[str, PlayerRotationProfile],
        player_profiles: Mapping[str, PlayerEventProfile],
        team_profiles: Mapping[str, TeamSimulationProfile],
    ) -> None:
        for player_id, player in state.players.items():
            if player.active and player.injury_state != "removed":
                if player_id not in rotation_profiles:
                    raise ValueError(f"missing rotation profile: {player_id}")
                if player_id not in player_profiles:
                    raise ValueError(f"missing event profile: {player_id}")
                rotation_profiles[player_id].validate()
                player_profiles[player_id].validate()
        for team_id in (state.config.home_team, state.config.away_team):
            if team_id not in team_profiles:
                raise ValueError(f"missing team profile: {team_id}")
            team_profiles[team_id].validate()


def player_stat_pmf(
    paths: Sequence[SimulationPath],
    player_id: str,
    stat: str,
) -> DiscretePMF:
    values = []
    for path in paths:
        player_stats = path.player_stats[player_id]
        if stat not in player_stats.__dataclass_fields__:
            raise ValueError(f"unknown stat: {stat}")
        values.append(int(getattr(player_stats, stat)))
    return DiscretePMF.from_samples(values)


def combination_pmf(
    paths: Sequence[SimulationPath],
    player_id: str,
    stats: Sequence[str],
) -> DiscretePMF:
    if not stats:
        raise ValueError("stats cannot be empty")
    values: list[int] = []
    for path in paths:
        box = path.player_stats[player_id]
        value = 0
        for stat in stats:
            if stat not in box.__dataclass_fields__:
                raise ValueError(f"unknown stat: {stat}")
            value += int(getattr(box, stat))
        values.append(value)
    return DiscretePMF.from_samples(values)


def game_market_pmf(
    paths: Sequence[SimulationPath],
    extractor: Callable[[SimulationPath], int],
) -> DiscretePMF:
    return DiscretePMF.from_samples([extractor(path) for path in paths])


def monte_carlo_standard_error(probability: float, simulations: int) -> float:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    if not 0 <= probability <= 1:
        raise ValueError("probability must be in [0, 1]")
    return math.sqrt(probability * (1 - probability) / simulations)
