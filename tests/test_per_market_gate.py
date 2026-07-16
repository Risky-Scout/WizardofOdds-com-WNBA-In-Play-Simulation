from __future__ import annotations

import random

from wizard_wnba.oos_validation import (
    PerMarketGate,
    evaluate_by_market,
    evaluate_market,
    prepare_oos_rows,
)


def _row(market, prob, outcome, *, selected=False, game="g0", stale=False,
         available=True, consensus=None, dec=2.0, result=None):
    return {
        "market_key": market,
        "market_eligible": True,
        "probability": prob,
        "outcome": outcome,
        "weight": 1.0,
        "selected": selected,
        "offered_decimal_odds": dec,
        "result": result or ("win" if outcome == 1 else "loss"),
        "game_id": game,
        "stale": stale,
        "available": available,
        "consensus_probability": consensus,
    }


def _cell(rows, market, prob, count, *, stale, available, rng, n_games,
          selected=False, dec=2.0):
    """Emit `count` rows EXACTLY calibrated at `prob` (round(prob*count) ones),
    with random cluster ids. Exact calibration in every stale/available cell
    makes the residual-bias gate see zero difference by construction."""
    ones = round(prob * count)
    for i in range(count):
        y = 1 if i < ones else 0
        rows.append(_row(
            market, prob, y, selected=selected, dec=dec,
            result="win" if y == 1 else "loss",
            game=f"{'bet' if selected else 'cal'}-{rng.randrange(n_games)}",
            stale=stale, available=available,
            consensus=(prob + 0.06) if selected else None,
        ))


def _calibrated_market(market, n_selected=400, n_games=40, seed=7):
    """Well-calibrated market that cleanly clears the per-market gate: every
    stale/available subgroup is exactly calibrated (zero residual bias), and
    n_selected calibrated +EV bets at decimal 2.2 give ROI +0.32 with a
    positive cluster bootstrap. Cluster ids are randomized so returns don't
    correlate with clusters."""
    rng = random.Random(seed)
    rows: list = []
    for level in (0.25, 0.5, 0.75):
        _cell(rows, market, level, 300, stale=False, available=True,
              rng=rng, n_games=n_games)
        _cell(rows, market, level, 60, stale=True, available=True,
              rng=rng, n_games=n_games)
        _cell(rows, market, level, 60, stale=False, available=False,
              rng=rng, n_games=n_games)
    if n_selected:
        _cell(rows, market, 0.6, n_selected, stale=False, available=True,
              selected=True, dec=2.2, rng=rng, n_games=n_games)
    return rows


def test_well_calibrated_market_passes():
    rows = _calibrated_market("h2h")
    result = evaluate_market(rows, market="h2h")
    assert result.selected_bets >= 100
    assert result.bootstrap_roi_lower_95 is not None
    assert result.bootstrap_roi_lower_95 > 0
    assert 0.85 <= result.calibration_slope <= 1.15
    assert result.passed, result.blockers


def test_too_few_selected_fails():
    rows = _calibrated_market("h2h", n_selected=40)
    result = evaluate_market(rows, market="h2h")
    assert result.selected_bets == 40
    assert "selected_bets_below_minimum" in result.blockers
    assert not result.passed


def test_miscalibrated_slope_fails():
    # Overconfident model: wide probability spread but empirical rates are
    # compressed toward 0.5 -> calibration slope well below 0.85.
    rows = []
    for level, rate in ((0.05, 0.45), (0.5, 0.5), (0.95, 0.55)):
        ones = round(rate * 300)
        for i in range(300):
            rows.append(_row("totals", level, 1 if i < ones else 0,
                             game=f"g{i % 30}"))
    result = evaluate_market(rows, market="totals")
    assert result.calibration_slope is not None
    assert result.calibration_slope < 0.85
    assert "calibration_slope_outside_gate" in result.blockers
    assert not result.passed


def test_negative_bootstrap_fails():
    rows = _calibrated_market("spreads", n_selected=0)
    # 120 losing selected bets -> negative ROI + bootstrap
    for i in range(120):
        rows.append(_row("spreads", 0.4, 0, selected=True, dec=1.9,
                         result="loss", game=f"b{i % 30}"))
    result = evaluate_market(rows, market="spreads")
    assert result.after_vig_roi is not None and result.after_vig_roi < 0
    assert "bootstrap_lower_roi_not_positive" in result.blockers
    assert not result.passed


def test_prepare_filters_ineligible_and_flagged():
    rows = [
        _row("h2h", 0.5, 1),
        _row("player_assists", 0.5, 1),                 # not eligible
        {**_row("totals", 0.5, 1), "market_eligible": False},  # flagged out
        {**_row("spreads", 0.5, 1), "outcome": None},   # unsettled
    ]
    prepared = prepare_oos_rows(rows)
    keys = [r["market_key"] for r in prepared]
    assert keys == ["h2h"]


def test_evaluate_by_market_covers_all_eligible():
    rows = _calibrated_market("h2h") + _calibrated_market("spreads")
    results = evaluate_by_market(rows)
    assert set(results) == {
        "h2h", "spreads", "totals", "player_points",
        "player_rebounds", "player_threes",
    }
    # Markets with no rows are present but fail (no data).
    assert results["totals"].sample_size == 0
    assert not results["totals"].passed


def test_per_market_gate_is_stricter_than_pooled():
    # Documented invariant: per-market min selected (100) applied to EACH market
    # is stricter than one pooled 200 spread across markets.
    gate = PerMarketGate()
    assert gate.minimum_selected_bets == 100
    assert gate.calibration_slope_min == 0.85
    assert gate.calibration_slope_max == 1.15
    assert gate.minimum_bootstrap_lower_roi == 0.0
