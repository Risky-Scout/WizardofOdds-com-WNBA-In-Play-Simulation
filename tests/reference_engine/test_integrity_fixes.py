from __future__ import annotations

from dataclasses import replace

from wnba_inplay.demo import build_demo_request
from wnba_inplay.service import SimulationService


def test_time_decay_edge_requires_external_reference_price():
    request = build_demo_request(simulations=100, seed=80)
    report = SimulationService().run(request)
    assert all(
        market.time_decay_adjusted_edge is None
        for market in report.markets
    )

    first = replace(
        request.markets[0],
        reference_decimal_odds=2.10,
    )
    request = replace(
        request,
        markets=(first,) + request.markets[1:],
    )
    report = SimulationService().run(request)
    assert report.markets[0].time_decay_adjusted_edge is not None


def test_actual_monte_carlo_error_can_suspend_quote():
    request = build_demo_request(simulations=20, seed=81)
    market_id = request.markets[0].market_id
    contexts = dict(request.quote_contexts)
    contexts[market_id] = replace(
        contexts[market_id],
        max_monte_carlo_error=0.0001,
    )
    request = replace(request, quote_contexts=contexts)
    report = SimulationService().run(request)
    market = next(
        item for item in report.markets if item.market_id == market_id
    )
    assert market.quote_status == "SUSPENDED"
    assert "MONTE_CARLO_ERROR_LIMIT" in market.quote_reasons
