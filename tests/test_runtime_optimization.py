from wizard_wnba.optimization import (
    blend_conditional_probability,
    consensus_for_market,
    deterministic_seed,
    should_escalate_simulations,
)


def test_deterministic_seed_is_stable() -> None:
    first = deterministic_seed(
        base_seed=17,
        state_fingerprint="game-state",
    )
    second = deterministic_seed(
        base_seed=17,
        state_fingerprint="game-state",
    )
    assert first == second


def test_blend_probability_stays_between_inputs() -> None:
    value = blend_conditional_probability(0.40, 0.60, 0.50)
    assert 0.40 < value < 0.60


def test_adaptive_simulation_escalation() -> None:
    assert should_escalate_simulations(
        requested_simulations=20_000,
        monte_carlo_error=0.004,
        expected_roi=0.01,
    )
    assert not should_escalate_simulations(
        requested_simulations=100_000,
        monte_carlo_error=0.004,
        expected_roi=0.01,
    )


def test_consensus_excludes_selected_book() -> None:
    selected = {
        "event_id": "e1",
        "market_key": "totals",
        "line": 150.5,
        "side": "over",
        "bookmaker_key": "a",
    }
    markets = []
    for book, over, under in (
        ("a", -110, -110),
        ("b", -105, -115),
        ("c", 100, -120),
    ):
        markets.extend(
            (
                {
                    **selected,
                    "bookmaker_key": book,
                    "side": "over",
                    "american_odds": over,
                },
                {
                    **selected,
                    "bookmaker_key": book,
                    "side": "under",
                    "american_odds": under,
                },
            )
        )
    estimate = consensus_for_market(
        {"markets": markets},
        selected,
        elapsed_fraction=0.5,
    )
    assert estimate.probability is not None
    assert estimate.book_count == 2
