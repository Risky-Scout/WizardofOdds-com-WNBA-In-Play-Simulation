from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .contracts import FORBIDDEN_PUBLIC_FIELDS, MarketOutput, RunMetadata


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reasons: tuple[str, ...]
    duplicate_pmfs: int
    invalid_pmfs: int
    duplicate_market_rows: int
    stale_artifacts: int


def validate_outputs(
    metadata: RunMetadata,
    outputs: Sequence[MarketOutput],
    expected_market_ids: Sequence[str],
) -> ValidationResult:
    reasons: list[str] = []
    metadata.validate()

    ids = [output.market_id for output in outputs]
    duplicate_market_rows = len(ids) - len(set(ids))
    if duplicate_market_rows:
        reasons.append("DUPLICATE_MARKET_ROWS")

    expected = set(expected_market_ids)
    actual = set(ids)
    missing = expected - actual
    unexpected = actual - expected
    if missing:
        reasons.append("MISSING_EXPECTED_MARKETS")
    if unexpected:
        reasons.append("UNEXPECTED_MARKETS")

    duplicate_pmfs = duplicate_market_rows
    invalid_pmfs = 0
    stale_artifacts = 0

    for output in outputs:
        probabilities = list(output.pmf.values())
        if (
            not probabilities
            or any(
                not math.isfinite(value) or value < 0
                for value in probabilities
            )
            or not math.isclose(sum(probabilities), 1.0, abs_tol=1e-12)
        ):
            invalid_pmfs += 1
            continue

        total = output.p_over + output.p_push + output.p_under
        if not math.isclose(total, 1.0, abs_tol=1e-12):
            invalid_pmfs += 1

        if float(output.line).is_integer():
            pmf_push = output.pmf.get(int(output.line), 0.0)
            if not math.isclose(
                pmf_push,
                output.p_push,
                abs_tol=1e-12,
            ):
                invalid_pmfs += 1

        if (
            output.run_id != metadata.run_id
            or output.commit_sha != metadata.commit_sha
        ):
            stale_artifacts += 1

    if invalid_pmfs:
        reasons.append("INVALID_PMFS")
    if stale_artifacts:
        reasons.append("STALE_ARTIFACT_METADATA")

    return ValidationResult(
        valid=not reasons,
        reasons=tuple(reasons),
        duplicate_pmfs=duplicate_pmfs,
        invalid_pmfs=invalid_pmfs,
        duplicate_market_rows=duplicate_market_rows,
        stale_artifacts=stale_artifacts,
    )


def find_forbidden_public_fields(value: Any) -> set[str]:
    found: set[str] = set()

    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_PUBLIC_FIELDS:
                found.add(str(key))
            found |= find_forbidden_public_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            found |= find_forbidden_public_fields(child)

    return found


def validate_report_dict(report: Mapping[str, Any]) -> ValidationResult:
    reasons: list[str] = []
    forbidden = find_forbidden_public_fields(report)
    if forbidden:
        reasons.append("FORBIDDEN_PUBLIC_FIELD")

    markets = report.get("markets")
    metadata = report.get("metadata")
    if not isinstance(markets, list) or not isinstance(metadata, Mapping):
        reasons.append("INVALID_REPORT_SHAPE")
        return ValidationResult(
            valid=False,
            reasons=tuple(reasons),
            duplicate_pmfs=0,
            invalid_pmfs=0,
            duplicate_market_rows=0,
            stale_artifacts=0,
        )

    ids = [
        market.get("market_id")
        for market in markets
        if isinstance(market, Mapping)
    ]
    duplicate_market_rows = len(ids) - len(set(ids))
    invalid_pmfs = 0
    stale = 0

    for market in markets:
        if not isinstance(market, Mapping):
            invalid_pmfs += 1
            continue
        pmf = market.get("pmf")
        if not isinstance(pmf, Mapping) or not pmf:
            invalid_pmfs += 1
            continue
        try:
            total = sum(float(value) for value in pmf.values())
        except (TypeError, ValueError):
            invalid_pmfs += 1
            continue
        if not math.isclose(total, 1.0, abs_tol=1e-12):
            invalid_pmfs += 1
        if (
            market.get("run_id") != metadata.get("run_id")
            or market.get("commit_sha") != metadata.get("commit_sha")
        ):
            stale += 1

    if duplicate_market_rows:
        reasons.append("DUPLICATE_MARKET_ROWS")
    if invalid_pmfs:
        reasons.append("INVALID_PMFS")
    if stale:
        reasons.append("STALE_ARTIFACT_METADATA")

    return ValidationResult(
        valid=not reasons,
        reasons=tuple(reasons),
        duplicate_pmfs=duplicate_market_rows,
        invalid_pmfs=invalid_pmfs,
        duplicate_market_rows=duplicate_market_rows,
        stale_artifacts=stale,
    )
