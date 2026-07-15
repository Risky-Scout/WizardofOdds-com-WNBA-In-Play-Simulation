from __future__ import annotations

from dataclasses import replace
import json
import math

from wnba_inplay.api import ReferenceAPI
from wnba_inplay.contracts import (
    MarketOutput,
    RunMetadata,
)
from wnba_inplay.demo import build_demo_request
from wnba_inplay.service import SimulationService
from wnba_inplay.validation import (
    find_forbidden_public_fields,
    validate_outputs,
    validate_report_dict,
)


def test_end_to_end_service_produces_valid_manifest():
    report = SimulationService().run(
        build_demo_request(simulations=80, seed=44)
    )
    assert report.manifest.status == "SUCCESS"
    assert report.manifest.expected_pmf_rows == 4
    assert report.manifest.actual_pmf_rows == 4
    assert report.manifest.duplicate_pmfs == 0
    assert report.manifest.invalid_pmfs == 0
    assert report.manifest.stale_artifacts == 0


def test_every_output_has_normalized_pmf_and_consistent_metadata():
    report = SimulationService().run(
        build_demo_request(simulations=80, seed=45)
    )
    for market in report.markets:
        assert math.isclose(sum(market.pmf.values()), 1.0)
        assert market.run_id == report.metadata.run_id
        assert market.commit_sha == report.metadata.commit_sha


def test_public_output_uses_time_decay_adjusted_edge_only():
    report = SimulationService().run(
        build_demo_request(simulations=40, seed=46)
    )
    payload = report.to_dict()
    assert not find_forbidden_public_fields(payload)
    assert all(
        "time_decay_adjusted_edge" in market
        for market in payload["markets"]
    )


def test_stale_metadata_is_detected():
    report = SimulationService().run(
        build_demo_request(simulations=30, seed=47)
    )
    stale = replace(report.markets[0], commit_sha="old-commit")
    outputs = (stale,) + report.markets[1:]
    result = validate_outputs(
        report.metadata,
        outputs,
        [market.market_id for market in report.markets],
    )
    assert not result.valid
    assert result.stale_artifacts == 1
    assert "STALE_ARTIFACT_METADATA" in result.reasons


def test_duplicate_market_is_detected():
    report = SimulationService().run(
        build_demo_request(simulations=30, seed=48)
    )
    outputs = report.markets + (report.markets[0],)
    result = validate_outputs(
        report.metadata,
        outputs,
        [market.market_id for market in report.markets],
    )
    assert not result.valid
    assert result.duplicate_market_rows == 1


def test_integer_line_push_matches_pmf():
    report = SimulationService().run(
        build_demo_request(simulations=100, seed=49)
    )
    integer_market = next(
        market
        for market in report.markets
        if float(market.line).is_integer()
    )
    assert integer_market.p_push == integer_market.pmf.get(
        int(integer_market.line),
        0.0,
    )


def test_health_and_capability_endpoints():
    api = ReferenceAPI()
    status, _, body = api.handle("GET", "/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"

    status, _, body = api.handle("GET", "/v1/capabilities")
    assert status == 200
    assert (
        json.loads(body)["public_edge_field"]
        == "time_decay_adjusted_edge"
    )


def test_validation_api_rejects_forbidden_legacy_field():
    report = SimulationService().run(
        build_demo_request(simulations=20, seed=50)
    ).to_dict()
    report["markets"][0]["clv_adj_edge"] = 0.01

    status, _, body = ReferenceAPI().handle(
        "POST",
        "/v1/validate-report",
        json.dumps(report).encode("utf-8"),
    )
    payload = json.loads(body)
    assert status == 422
    assert "FORBIDDEN_PUBLIC_FIELD" in payload["reasons"]


def test_report_dict_validator_accepts_service_output():
    report = SimulationService().run(
        build_demo_request(simulations=30, seed=51)
    ).to_dict()
    result = validate_report_dict(report)
    assert result.valid
