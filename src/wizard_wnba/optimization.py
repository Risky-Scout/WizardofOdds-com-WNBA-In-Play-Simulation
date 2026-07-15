from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from statistics import median
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ConsensusEstimate:
    probability: float | None
    book_count: int
    dispersion: float | None
    weight: float
    uncertainty: float | None
    probabilities: tuple[float, ...]


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _american_implied(odds: object) -> float | None:
    try:
        price = int(float(odds))
    except (TypeError, ValueError):
        return None
    if price == 0 or (-100 < price < 100):
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def _line_key(item: Mapping[str, Any]) -> float | None:
    market_key = str(item.get("market_key") or "")
    if market_key == "h2h":
        return None
    try:
        line = float(item.get("line"))
    except (TypeError, ValueError):
        return None
    if market_key == "spreads":
        line = abs(line)
    return round(line, 3)


def _subject_key(item: Mapping[str, Any]) -> str:
    market_key = str(item.get("market_key") or "")
    if market_key.startswith("player_"):
        return _normalize(
            item.get("player_name")
            or item.get("selection")
        )
    return ""


def _same_market_group(
    candidate: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> bool:
    return (
        str(candidate.get("event_id") or "")
        == str(selected.get("event_id") or "")
        and str(candidate.get("market_key") or "")
        == str(selected.get("market_key") or "")
        and _subject_key(candidate) == _subject_key(selected)
        and _line_key(candidate) == _line_key(selected)
    )


def _same_side(
    candidate: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> bool:
    return _normalize(candidate.get("side")) == _normalize(
        selected.get("side")
    )


def _median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = median(values)
    return median(abs(value - center) for value in values)


def consensus_for_market(
    market_feed: Mapping[str, Any],
    selected_market: Mapping[str, Any],
    *,
    elapsed_fraction: float,
) -> ConsensusEstimate:
    grouped: dict[str, list[Mapping[str, Any]]] = {}

    for item in market_feed.get("markets", []):
        if not isinstance(item, Mapping):
            continue
        if not _same_market_group(item, selected_market):
            continue
        bookmaker = str(item.get("bookmaker_key") or "")
        if bookmaker:
            grouped.setdefault(bookmaker, []).append(item)

    probabilities: list[tuple[str, float]] = []

    for bookmaker, items in grouped.items():
        selected_rows = [
            item for item in items if _same_side(item, selected_market)
        ]
        if len(selected_rows) != 1:
            continue

        implied = [
            value
            for value in (
                _american_implied(item.get("american_odds"))
                for item in items
            )
            if value is not None
        ]
        if len(implied) < 2:
            continue

        selected_implied = _american_implied(
            selected_rows[0].get("american_odds")
        )
        if selected_implied is None:
            continue

        total = sum(implied)
        if total <= 0:
            continue

        probabilities.append(
            (bookmaker, _clip(selected_implied / total, 0.001, 0.999))
        )

    selected_book = str(selected_market.get("bookmaker_key") or "")
    excluding_selected = [
        probability
        for bookmaker, probability in probabilities
        if bookmaker != selected_book
    ]
    working = (
        excluding_selected
        if len(excluding_selected) >= 2
        else [probability for _, probability in probabilities]
    )

    if len(working) < 2:
        return ConsensusEstimate(
            probability=None,
            book_count=len(working),
            dispersion=None,
            weight=0.0,
            uncertainty=None,
            probabilities=tuple(working),
        )

    center = median(working)
    mad = _median_absolute_deviation(working)
    robust_scale = max(0.01, 1.4826 * mad)
    filtered = [
        value
        for value in working
        if abs(value - center) <= 3.0 * robust_scale
    ]
    if len(filtered) >= 2:
        working = filtered
        center = median(working)
        mad = _median_absolute_deviation(working)
        robust_scale = max(0.01, 1.4826 * mad)

    book_count = len(working)
    elapsed = _clip(elapsed_fraction, 0.0, 1.0)
    raw_weight = (
        0.15
        + 0.08 * min(book_count, 5)
        + 0.15 * (1.0 - elapsed)
        - 2.0 * robust_scale
    )
    weight = _clip(raw_weight, 0.15, 0.60)
    uncertainty = max(
        0.015,
        robust_scale,
        0.04 / math.sqrt(book_count),
    )

    return ConsensusEstimate(
        probability=_clip(center, 0.001, 0.999),
        book_count=book_count,
        dispersion=robust_scale,
        weight=weight,
        uncertainty=uncertainty,
        probabilities=tuple(working),
    )


def blend_conditional_probability(
    raw_probability: float,
    consensus_probability: float,
    consensus_weight: float,
) -> float:
    raw = _clip(raw_probability, 1e-6, 1 - 1e-6)
    consensus = _clip(consensus_probability, 1e-6, 1 - 1e-6)
    weight = _clip(consensus_weight, 0.0, 1.0)

    raw_logit = math.log(raw / (1 - raw))
    consensus_logit = math.log(consensus / (1 - consensus))
    blended_logit = (
        (1 - weight) * raw_logit
        + weight * consensus_logit
    )
    return 1 / (1 + math.exp(-blended_logit))


def deterministic_seed(
    *,
    base_seed: int,
    state_fingerprint: str,
) -> int:
    payload = f"{base_seed}|{state_fingerprint}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFF


def should_escalate_simulations(
    *,
    requested_simulations: int,
    monte_carlo_error: float,
    expected_roi: float,
) -> bool:
    return (
        requested_simulations >= 20_000
        and requested_simulations < 100_000
        and (
            monte_carlo_error > 0.0035
            or -0.02 <= expected_roi <= 0.08
        )
    )


def confidence_label(total_uncertainty: float) -> str:
    if total_uncertainty <= 0.035:
        return "High"
    if total_uncertainty <= 0.060:
        return "Medium"
    return "Low"
