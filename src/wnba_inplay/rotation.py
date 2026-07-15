from __future__ import annotations

from dataclasses import dataclass
import math
import random
from statistics import fmean
from typing import Mapping, Sequence

from .domain import GameState, PlayerState


@dataclass(frozen=True)
class PlayerRotationProfile:
    player_id: str
    target_total_minutes: float
    rotation_weight: float = 1.0
    closing_priority: float = 0.0
    foul_sensitivity: float = 1.0
    blowout_sensitivity: float = 1.0
    minimum_remaining_seconds: int = 0

    def validate(self) -> None:
        if self.target_total_minutes < 0:
            raise ValueError("target_total_minutes cannot be negative")
        if self.rotation_weight <= 0:
            raise ValueError("rotation_weight must be positive")
        if self.minimum_remaining_seconds < 0:
            raise ValueError("minimum_remaining_seconds cannot be negative")


@dataclass(frozen=True)
class RotationContext:
    game_seconds_remaining: int
    score_margin: int
    overtime_probability: float = 0.0
    blowout_threshold: int = 18
    uncertainty_scale: float = 0.18

    def validate(self) -> None:
        if self.game_seconds_remaining < 0:
            raise ValueError("game_seconds_remaining cannot be negative")
        if not 0.0 <= self.overtime_probability <= 1.0:
            raise ValueError("overtime_probability must be in [0, 1]")
        if self.uncertainty_scale < 0:
            raise ValueError("uncertainty_scale cannot be negative")


@dataclass(frozen=True)
class RemainingMinutesSummary:
    player_id: str
    mean_seconds: float
    p10_seconds: float
    p50_seconds: float
    p90_seconds: float


def game_seconds_remaining(state: GameState) -> int:
    """Return regulation seconds remaining from the authoritative state."""
    current = state.clock_seconds
    future_periods = max(
        state.config.regulation_periods - state.period,
        0,
    )
    if state.period <= state.config.regulation_periods:
        return current + future_periods * state.config.regulation_period_seconds
    return current


class RotationAllocator:
    """Samples coherent remaining-minute allocations under lineup constraints.

    Every simulation allocates exactly five player-seconds per team-second.
    Individual players cannot receive more seconds than the simulated horizon.
    """

    _INJURY_MULTIPLIER = {
        "healthy": 1.0,
        "minor_limitation": 0.82,
        "material_limitation": 0.48,
        "probable_removal": 0.15,
        "removed": 0.0,
    }

    def sample(
        self,
        state: GameState,
        profiles: Mapping[str, PlayerRotationProfile],
        context: RotationContext,
        simulations: int,
        seed: int,
    ) -> list[dict[str, int]]:
        context.validate()
        if simulations <= 0:
            raise ValueError("simulations must be positive")
        for profile in profiles.values():
            profile.validate()

        rng = random.Random(seed)
        outputs: list[dict[str, int]] = []

        for _ in range(simulations):
            overtime = (
                state.config.overtime_period_seconds
                if rng.random() < context.overtime_probability
                else 0
            )
            horizon = context.game_seconds_remaining + overtime
            allocation: dict[str, int] = {}

            for team_id in (state.config.home_team, state.config.away_team):
                team_players = [
                    player
                    for player in state.players.values()
                    if player.team_id == team_id
                ]
                team_alloc = self._sample_team(
                    players=team_players,
                    profiles=profiles,
                    context=context,
                    horizon=horizon,
                    rng=rng,
                )
                allocation.update(team_alloc)

            outputs.append(allocation)

        return outputs

    def summarize(
        self,
        samples: Sequence[Mapping[str, int]],
    ) -> dict[str, RemainingMinutesSummary]:
        if not samples:
            raise ValueError("samples cannot be empty")
        player_ids = sorted({key for sample in samples for key in sample})
        summaries: dict[str, RemainingMinutesSummary] = {}

        for player_id in player_ids:
            values = sorted(float(sample.get(player_id, 0)) for sample in samples)
            summaries[player_id] = RemainingMinutesSummary(
                player_id=player_id,
                mean_seconds=fmean(values),
                p10_seconds=self._quantile(values, 0.10),
                p50_seconds=self._quantile(values, 0.50),
                p90_seconds=self._quantile(values, 0.90),
            )
        return summaries

    def substitution_hazard(
        self,
        player: PlayerState,
        profile: PlayerRotationProfile,
        score_margin: int,
        seconds_remaining: int,
    ) -> float:
        """Probability-like hazard for removal during the next short interval."""
        profile.validate()
        stint_pressure = (player.current_stint_seconds - 300) / 120
        foul_pressure = max(player.stats.fouls - 2, 0) * profile.foul_sensitivity
        injury_pressure = {
            "healthy": 0.0,
            "minor_limitation": 0.6,
            "material_limitation": 1.5,
            "probable_removal": 3.0,
            "removed": 10.0,
        }.get(player.injury_state, 0.0)
        blowout_pressure = (
            max(abs(score_margin) - 15, 0) / 5
            * profile.blowout_sensitivity
        )
        closing_relief = (
            profile.closing_priority
            if seconds_remaining <= 300
            else 0.0
        )
        logit = (
            -2.2
            + stint_pressure
            + 0.55 * foul_pressure
            + injury_pressure
            + 0.45 * blowout_pressure
            - closing_relief
        )
        return 1.0 / (1.0 + math.exp(-max(min(logit, 30), -30)))

    def _sample_team(
        self,
        players: Sequence[PlayerState],
        profiles: Mapping[str, PlayerRotationProfile],
        context: RotationContext,
        horizon: int,
        rng: random.Random,
    ) -> dict[str, int]:
        allocation = {player.player_id: 0 for player in players}
        required = 5 * horizon
        eligible = [
            player
            for player in players
            if player.active
            and player.injury_state != "removed"
            and player.player_id in profiles
        ]
        if required == 0:
            return allocation
        if len(eligible) < 5:
            raise ValueError("at least five eligible players are required per team")

        weights: list[float] = []
        floors: list[int] = []
        caps: list[int] = []

        blowout = abs(context.score_margin) >= context.blowout_threshold

        for player in eligible:
            profile = profiles[player.player_id]
            played = player.stats.minutes_seconds
            target_remaining = max(
                profile.target_total_minutes * 60 - played,
                1.0,
            )
            injury_multiplier = self._INJURY_MULTIPLIER[player.injury_state]
            foul_multiplier = math.exp(
                -0.10
                * max(player.stats.fouls - 2, 0)
                * profile.foul_sensitivity
            )
            blowout_multiplier = (
                math.exp(
                    -0.28
                    * profile.closing_priority
                    * profile.blowout_sensitivity
                )
                if blowout
                else 1.0
            )
            closing_multiplier = (
                1.0 + 0.30 * profile.closing_priority
                if context.game_seconds_remaining <= 300
                else 1.0
            )
            # Log-normal perturbation produces positive, asymmetric uncertainty.
            perturbation = math.exp(
                rng.gauss(
                    -0.5 * context.uncertainty_scale**2,
                    context.uncertainty_scale,
                )
            )
            weight = (
                target_remaining
                * profile.rotation_weight
                * injury_multiplier
                * foul_multiplier
                * blowout_multiplier
                * closing_multiplier
                * perturbation
            )
            weights.append(max(weight, 1e-9))
            floors.append(min(profile.minimum_remaining_seconds, horizon))
            caps.append(horizon)

        if sum(floors) > required:
            raise ValueError("minimum remaining seconds exceed team capacity")
        if sum(caps) < required:
            raise ValueError("eligible player capacity is below team requirement")

        continuous = self._capped_proportional_allocation(
            total=required,
            weights=weights,
            floors=floors,
            caps=caps,
        )
        integer = self._largest_remainder(continuous, required, caps)

        for player, seconds in zip(eligible, integer, strict=True):
            allocation[player.player_id] = seconds

        if sum(allocation.values()) != required:
            raise RuntimeError("rotation allocation failed team-sum invariant")
        if any(value < 0 or value > horizon for value in allocation.values()):
            raise RuntimeError("rotation allocation violated player bounds")
        return allocation

    @staticmethod
    def _capped_proportional_allocation(
        total: int,
        weights: Sequence[float],
        floors: Sequence[int],
        caps: Sequence[int],
    ) -> list[float]:
        values = [float(x) for x in floors]
        remaining = float(total - sum(floors))
        active = {index for index, cap in enumerate(caps) if values[index] < cap}

        while remaining > 1e-9 and active:
            weight_sum = sum(weights[index] for index in active)
            if weight_sum <= 0:
                shares = {index: remaining / len(active) for index in active}
            else:
                shares = {
                    index: remaining * weights[index] / weight_sum
                    for index in active
                }

            consumed = 0.0
            saturated: set[int] = set()
            for index in active:
                available = caps[index] - values[index]
                addition = min(shares[index], available)
                values[index] += addition
                consumed += addition
                if available - addition <= 1e-9:
                    saturated.add(index)

            if consumed <= 1e-12:
                break
            remaining -= consumed
            active -= saturated

        if abs(sum(values) - total) > 1e-6:
            raise RuntimeError("unable to satisfy capped allocation")
        return values

    @staticmethod
    def _largest_remainder(
        continuous: Sequence[float],
        total: int,
        caps: Sequence[int],
    ) -> list[int]:
        integers = [math.floor(value) for value in continuous]
        missing = total - sum(integers)
        order = sorted(
            range(len(continuous)),
            key=lambda index: continuous[index] - integers[index],
            reverse=True,
        )
        for index in order:
            if missing <= 0:
                break
            if integers[index] < caps[index]:
                integers[index] += 1
                missing -= 1
        if missing != 0:
            raise RuntimeError("integer allocation could not preserve total")
        return integers

    @staticmethod
    def _quantile(values: Sequence[float], probability: float) -> float:
        if not values:
            raise ValueError("values cannot be empty")
        if len(values) == 1:
            return values[0]
        position = (len(values) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return values[lower]
        fraction = position - lower
        return values[lower] * (1 - fraction) + values[upper] * fraction
