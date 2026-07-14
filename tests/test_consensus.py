from __future__ import annotations

from datetime import UTC, datetime

from wizard_wnba.consensus import build_consensus, two_way_no_vig
from wizard_wnba.domain import MarketOffer
from wizard_wnba.odds_math import american_to_decimal


def offer(book: str, side: str, american: int) -> MarketOffer:
    now = datetime.now(UTC)
    return MarketOffer(
        provider="test",
        bookmaker_key=book,
        bookmaker_title=book.title(),
        provider_event_id="event",
        canonical_game_id="game",
        market_key="player_points",
        outcome_name=side.title(),
        side=side,
        line=20.5,
        american_odds=american,
        decimal_odds=american_to_decimal(american),
        player_name="Player One",
        canonical_player_id="p1",
        last_update=now,
        received_timestamp=now,
    )


def test_two_way_no_vig_sums_to_one():
    over, under = two_way_no_vig(
        offer("a", "over", -110),
        offer("a", "under", -110),
    )
    assert over + under == 1
    assert over == .5


def test_leave_one_book_out_consensus():
    offers = [
        offer("a", "over", -110),
        offer("a", "under", -110),
        offer("b", "over", 100),
        offer("b", "under", -120),
        offer("c", "over", 105),
        offer("c", "under", -125),
    ]
    output = build_consensus(
        offers,
        target_side="over",
        exclude_bookmaker="a",
    )
    result = output[("game", "player_points", "p1", 20.5)]
    assert result.book_count == 2
    assert result.best_bookmaker == "c"
