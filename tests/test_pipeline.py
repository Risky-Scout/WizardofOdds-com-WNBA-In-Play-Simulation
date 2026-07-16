from __future__ import annotations

from datetime import UTC, datetime

from wizard_wnba.domain import (
    GameState,
    MarketOffer,
    ProbabilityEstimate,
    RecommendationStatus,
)
from wizard_wnba.pipeline import (
    POSSESSION_PROBABILITY_SOURCE,
    ModelMetadata,
    RecommendationPipeline,
)


def _model() -> ModelMetadata:
    # Eligible markets = keys of calibration_parameters (near-identity Platt).
    params = {
        market: (1.0, -1.0, 0.0)
        for market in ("h2h", "player_points", "spreads", "totals")
    }
    return ModelMetadata(
        model_version="v-test",
        calibrator_id="cal-test",
        calibration_score=0.9,
        calibration_se=0.01,
        model_se=0.02,
        calibration_parameters=params,
    )


def _estimate(market_key: str, line: float | None) -> ProbabilityEstimate:
    return ProbabilityEstimate(
        market_key=market_key,
        line=line,
        p_win=0.5,
        p_push=0.0,
        p_loss=0.5,
        fair_decimal_odds=2.0,
        projected_mean=1.0,
        projected_sd=1.0,
        simulation_count=1000,
        monte_carlo_se=0.01,
        calibration_se=0.01,
        model_se=0.02,
        calibration_score=0.9,
        calibrator_id="raw",
        model_version="v-test",
    )


class StubSimulator:
    def simulate(self, **kwargs):
        return object()

    def price_game_market(self, **kwargs):
        return _estimate(kwargs["market_key"], kwargs["line"])

    def price_player_market(self, **kwargs):
        return _estimate(kwargs["market_key"], kwargs["line"])


class StubPolicy:
    def __init__(self) -> None:
        self.evaluated: list[str] = []

    def evaluate(self, *, game, offer, probability, consensus):
        self.evaluated.append(offer.market_key)
        # A lightweight stand-in for a Recommendation.
        return object()


def _game() -> GameState:
    now = datetime.now(UTC)
    return GameState(
        canonical_game_id="bdl-1",
        source_game_id="1",
        home_team="HOME",
        away_team="AWAY",
        period=2,
        clock_seconds=300.0,
        home_score=40,
        away_score=38,
        possession_team=None,
        event_sequence=10,
        source_timestamp=now,
        received_timestamp=now,
    )


def _offer(market_key: str, *, line, player_id=None, side="over") -> MarketOffer:
    now = datetime.now(UTC)
    return MarketOffer(
        provider="the_odds_api",
        bookmaker_key="draftkings",
        bookmaker_title="DraftKings",
        provider_event_id="evt-1",
        canonical_game_id="bdl-1",
        market_key=market_key,
        outcome_name="Over",
        side=side,
        line=line,
        american_odds=-110,
        decimal_odds=1.91,
        player_name="WAS P1" if player_id else None,
        canonical_player_id=player_id,
        last_update=now,
        received_timestamp=now,
    )


def _run(offers):
    policy = StubPolicy()
    pipeline = RecommendationPipeline(simulator=StubSimulator(), policy=policy)
    recs = pipeline.evaluate_game(
        game=_game(),
        live_players={},
        profiles={},
        offers=offers,
        model=_model(),
        simulations=1000,
        seed=1,
    )
    return policy, recs


def test_unsupported_market_is_skipped_not_fatal():
    # player_assists has no calibrator -> not eligible -> skipped silently.
    offers = [
        _offer("h2h", line=None),
        _offer("player_assists", line=15.5, player_id="bdl-player-1"),
    ]
    policy, recs = _run(offers)
    assert "h2h" in policy.evaluated
    assert "player_assists" not in policy.evaluated
    # The eligible market still produced a recommendation; no exception raised.
    assert len(recs) == 1


def test_pra_market_is_skipped():
    offers = [
        _offer(
            "player_points_rebounds_assists",
            line=30.5,
            player_id="bdl-player-1",
        ),
    ]
    policy, recs = _run(offers)
    assert policy.evaluated == []
    assert recs == ()


def test_all_eligible_markets_priced():
    offers = [
        _offer("h2h", line=None),
        _offer("totals", line=160.5),
        _offer("spreads", line=-3.5),
    ]
    policy, recs = _run(offers)
    assert set(policy.evaluated) == {"h2h", "totals", "spreads"}
    assert len(recs) == 3


def test_model_probability_source_defaults_to_possession_raw():
    assert _model().probability_source == POSSESSION_PROBABILITY_SOURCE
    assert POSSESSION_PROBABILITY_SOURCE == "possession_raw_probability"


def test_model_eligible_markets_exclude_forbidden():
    eligible = _model().eligible_markets
    assert "player_assists" not in eligible
    assert "player_points_rebounds_assists" not in eligible
    assert "h2h" in eligible
