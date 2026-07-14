from __future__ import annotations

import math
import pytest

from wizard_wnba.odds_math import (
    american_to_decimal,
    decimal_to_american,
    expected_roi,
    fair_decimal_with_push,
)


def test_american_decimal_round_trip():
    for value in (-200, -110, 100, 150, 300):
        decimal = american_to_decimal(value)
        assert decimal_to_american(decimal) == value


def test_push_aware_fair_odds():
    assert math.isclose(fair_decimal_with_push(0.45, 0.10), 2.0)


def test_expected_roi_treats_push_as_returned_stake():
    roi = expected_roi(0.50, 0.10, 0.40, 2.0)
    assert math.isclose(roi, 0.10)


def test_zero_american_odds_rejected():
    with pytest.raises(ValueError):
        american_to_decimal(0)
