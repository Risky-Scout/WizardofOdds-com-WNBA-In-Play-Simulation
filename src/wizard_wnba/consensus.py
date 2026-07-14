from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from statistics import median, pstdev
from typing import Iterable

from .domain import MarketConsensus, MarketOffer
from .odds_math import implied_probability


def _match_key(offer: MarketOffer) -> tuple[str, str, str | None, float | None]:
    return (
        offer.canonical_game_id,
        offer.market_key,
        offer.canonical_player_id,
        offer.line,
    )


def two_way_no_vig(over: MarketOffer, under: MarketOffer) -> tuple[float, float]:
    q_over = implied_probability(over.decimal_odds)
    q_under = implied_probability(under.decimal_odds)
    denominator = q_over + q_under
    if denominator <= 0:
        raise ValueError("invalid implied probabilities")
    return q_over / denominator, q_under / denominator


def build_consensus(
    offers: Iterable[MarketOffer],
    *,
    target_side: str,
    exclude_bookmaker: str | None = None,
) -> dict[tuple[str, str, str | None, float | None], MarketConsensus]:
    grouped: dict[
        tuple[str, str, str | None, float | None],
        dict[str, dict[str, MarketOffer]],
    ] = defaultdict(lambda: defaultdict(dict))

    for offer in offers:
        if not offer.available:
            continue
        if exclude_bookmaker and offer.bookmaker_key == exclude_bookmaker:
            continue
        grouped[_match_key(offer)][offer.bookmaker_key][offer.side.lower()] = offer

    output = {}
    for key, by_book in grouped.items():
        probabilities: list[float] = []
        target_odds: list[tuple[float, str]] = []

        for bookmaker, sides in by_book.items():
            if "over" in sides and "under" in sides:
                p_over, p_under = two_way_no_vig(sides["over"], sides["under"])
                probability = p_over if target_side == "over" else p_under
                probabilities.append(probability)
            if target_side in sides:
                target_odds.append((sides[target_side].decimal_odds, bookmaker))

        if target_odds:
            best_decimal, best_book = max(target_odds)
            median_decimal = median(value for value, _ in target_odds)
        else:
            best_decimal = None
            best_book = None
            median_decimal = None

        output[key] = MarketConsensus(
            market_key=key[1],
            line=key[3],
            side=target_side,
            no_vig_probability=median(probabilities) if probabilities else None,
            book_count=len(probabilities),
            median_decimal_odds=median_decimal,
            best_decimal_odds=best_decimal,
            best_bookmaker=best_book,
            dispersion=pstdev(probabilities) if len(probabilities) > 1 else 0.0
            if probabilities
            else None,
            as_of=datetime.now(UTC),
        )
    return output
