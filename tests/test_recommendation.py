from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from wizard_wnba.domain import (
    GameState,
    MarketConsensus,
    MarketOffer,
    ProbabilityEstimate,
    RecommendationStatus,
)
from wizard_wnba.recommendation import AdaptivePolicy


def objects():
    now = datetime.now(UTC)
    game = GameState(
        canonical_game_id="g",
        source_game_id="1",
        home_team="Home",
        away_team="Away",
        period=3,
        clock_seconds=300,
        home_score=60,
        away_score=58,
        possession_team="Home",
        event_sequence=10,
        source_timestamp=now,
        received_timestamp=now,
    )
    offer = MarketOffer(
        provider="test",
        bookmaker_key="book",
        bookmaker_title="Book",
        provider_event_id="e",
        canonical_game_id="g",
        market_key="player_points",
        outcome_name="Over",
        side="over",
        line=20.5,
        american_odds=100,
        decimal_odds=2.0,
        player_name="Player",
        canonical_player_id="p",
        last_update=now,
        received_timestamp=now,
    )
    probability = ProbabilityEstimate(
        market_key="player_points",
        line=20.5,
        p_win=.60,
        p_push=0,
        p_loss=.40,
        fair_decimal_odds=1/.60,
        projected_mean=22,
        projected_sd=4,
        simulation_count=20000,
        monte_carlo_se=.0035,
        calibration_se=.003,
        model_se=.004,
        calibration_score=.92,
        calibrator_id="cal",
        model_version="v1",
    )
    consensus = MarketConsensus(
        market_key="player_points",
        line=20.5,
        side="over",
        no_vig_probability=.52,
        book_count=4,
        median_decimal_odds=1.95,
        best_decimal_odds=2.0,
        best_bookmaker="book",
        dispersion=.02,
        as_of=now,
    )
    return game, offer, probability, consensus


def test_adaptive_policy_publishes_only_above_required_conservative_roi():
    game, offer, probability, consensus = objects()
    recommendation = AdaptivePolicy().evaluate(
        game=game,
        offer=offer,
        probability=probability,
        consensus=consensus,
    )
    assert recommendation.status == RecommendationStatus.PUBLISHED
    assert recommendation.required_roi >= .02
    assert recommendation.conservative_roi >= recommendation.required_roi


def test_hard_minimum_is_never_below_two_percent():
    game, offer, probability, consensus = objects()
    policy = AdaptivePolicy(
        hard_min_conservative_roi=.02,
        base_conservative_roi=.0,
    )
    assert policy.required_roi(
        probability=probability,
        consensus=consensus,
        game=game,
        offer=offer,
    ) >= .02


def test_stale_market_suspends_even_with_large_edge():
    game, offer, probability, consensus = objects()
    offer = replace(
        offer,
        received_timestamp=datetime.now(UTC) - timedelta(seconds=60),
    )
    recommendation = AdaptivePolicy(max_market_age_seconds=10).evaluate(
        game=game,
        offer=offer,
        probability=probability,
        consensus=consensus,
    )
    assert recommendation.status == RecommendationStatus.SUSPENDED
    assert "STALE_MARKET" in recommendation.reasons


def test_low_edge_stays_watch():
    game, offer, probability, consensus = objects()
    probability = replace(probability, p_win=.51, p_loss=.49)
    recommendation = AdaptivePolicy().evaluate(
        game=game,
        offer=offer,
        probability=probability,
        consensus=consensus,
    )
    assert recommendation.status == RecommendationStatus.WATCH
