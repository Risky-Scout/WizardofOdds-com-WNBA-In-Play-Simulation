from __future__ import annotations

import math


def american_to_decimal(american: int) -> float:
    if american == 0:
        raise ValueError("American odds cannot be zero")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1.0:
        raise ValueError("Decimal odds must exceed 1.0")
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100)
    return round(-100.0 / (decimal - 1.0))


def implied_probability(decimal: float) -> float:
    if decimal <= 1.0:
        raise ValueError("Decimal odds must exceed 1.0")
    return 1.0 / decimal


def fair_decimal_with_push(p_win: float, p_push: float) -> float:
    if p_win < 0 or p_push < 0 or p_win + p_push > 1 + 1e-12:
        raise ValueError("invalid probabilities")
    if p_win == 0:
        return math.inf
    return (1.0 - p_push) / p_win


def expected_roi(
    p_win: float,
    p_push: float,
    p_loss: float,
    decimal_odds: float,
) -> float:
    if decimal_odds <= 1:
        raise ValueError("decimal odds must exceed one")
    if not math.isclose(p_win + p_push + p_loss, 1.0, abs_tol=1e-9):
        raise ValueError("probabilities must sum to one")
    return p_win * (decimal_odds - 1.0) - p_loss
