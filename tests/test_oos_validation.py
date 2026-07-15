import random

from wizard_wnba.oos_validation import (
    ValidationThresholds,
    evaluate_oos_rows,
)


def _rows(count: int = 1200):
    rng = random.Random(42)
    rows = []
    for index in range(count):
        probability = 0.58 if index % 2 == 0 else 0.42
        outcome = 1 if rng.random() < probability else 0
        selected = probability >= 0.55
        rows.append(
            {
                "game_id": f"g-{index // 4}",
                "probability": probability,
                "consensus_probability": 0.50,
                "outcome": outcome,
                "selected": selected,
                "offered_decimal_odds": 2.20,
                "result": "win" if outcome else "loss",
                "stale": index % 4 == 0,
                "available": index % 5 != 0,
            }
        )
    return rows


def test_oos_gate_computes_required_metrics() -> None:
    metrics = evaluate_oos_rows(
        _rows(),
        thresholds=ValidationThresholds(
            minimum_rows=100,
            minimum_selected_bets=50,
            bootstrap_samples=250,
            minimum_bootstrap_lower_roi=-1.0,
            maximum_stale_residual_bias=1.0,
            maximum_availability_residual_bias=1.0,
        ),
    )
    assert metrics.sample_size == 1200
    assert metrics.consensus_brier is not None
    assert metrics.after_vig_roi is not None
    assert metrics.bootstrap_roi_lower_95 is not None
    assert metrics.stale_bias_measurable
    assert metrics.availability_bias_measurable
