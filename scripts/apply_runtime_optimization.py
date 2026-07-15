from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_REFERENCE = ROOT / "src" / "wizard_wnba" / "live_reference.py"
ENV_EXAMPLE = ROOT / ".env.example"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Could not locate patch marker: {label}")
    return text.replace(old, new, 1)


def patch_live_reference() -> None:
    text = LIVE_REFERENCE.read_text(encoding="utf-8")

    import_marker = "from .odds_math import american_to_decimal\n"
    import_block = """from .odds_math import american_to_decimal
from .optimization import (
    blend_conditional_probability,
    confidence_label,
    consensus_for_market,
    deterministic_seed,
    should_escalate_simulations,
)
"""
    text = replace_once(
        text,
        import_marker,
        import_block,
        "optimization imports",
    )

    game_marker = """    if game is None:
        raise LiveReferenceError(
            "The live game state is not present in the current snapshot."
        )

    source_game_id = canonical_game_id.removeprefix("bdl-")
"""
    game_replacement = """    if game is None:
        raise LiveReferenceError(
            "The live game state is not present in the current snapshot."
        )

    state_age_seconds = max(
        0.0,
        _number(game.get("state_age_seconds")),
    )
    if state_age_seconds > 20.0:
        raise LiveReferenceError(
            "The live game state is more than 20 seconds old."
        )

    source_file_value = str(market.get("source_file") or "").strip()
    price_age_seconds: float | None = None
    if source_file_value:
        source_path = Path(source_file_value)
        if source_path.exists():
            price_age_seconds = max(
                0.0,
                datetime.now(UTC).timestamp()
                - source_path.stat().st_mtime,
            )
            if price_age_seconds > 30.0:
                raise LiveReferenceError(
                    "The selected sportsbook price is more than 30 seconds old."
                )

    source_game_id = canonical_game_id.removeprefix("bdl-")
"""
    text = replace_once(
        text,
        game_marker,
        game_replacement,
        "staleness gates",
    )

    metadata_marker = """    metadata = RunMetadata(
        run_id=run_id,
        commit_sha="integrated-live-reference",
        model_version="possession-reference-v1",
        calibration_version="not-yet-oos-calibrated",
        event_sequence=state.sequence,
        as_of_timestamp_ms=now_ms,
        source_timestamp_ms=min(
            now_ms,
            max(0, state.last_source_timestamp_ms),
        ),
        input_fingerprint=fingerprint(state.to_dict()),
    )
"""
    metadata_replacement = """    state_fingerprint = fingerprint(state.to_dict())
    simulation_seed = deterministic_seed(
        base_seed=seed,
        state_fingerprint=state_fingerprint,
    )
    metadata = RunMetadata(
        run_id=run_id,
        commit_sha="integrated-live-reference",
        model_version="possession-reference-v2",
        calibration_version="market-consensus-anchor-v1-not-oos",
        event_sequence=state.sequence,
        as_of_timestamp_ms=now_ms,
        source_timestamp_ms=min(
            now_ms,
            max(0, state.last_source_timestamp_ms),
        ),
        input_fingerprint=state_fingerprint,
    )
"""
    text = replace_once(
        text,
        metadata_marker,
        metadata_replacement,
        "deterministic metadata",
    )

    run_start = text.find("    report = SimulationService().run(")
    return_start = text.find("    return {", run_start)
    if run_start < 0 or return_start < 0:
        if "market_consensus_probability" not in text:
            raise RuntimeError("Could not locate simulation execution block")
    else:
        new_run_block = """    service = SimulationService()

    def execute(count: int):
        return service.run(
            SimulationRequest(
                state=state,
                rotation_profiles=rotations,
                player_profiles=players,
                team_profiles=teams,
                markets=(spec,),
                quote_contexts={spec.market_id: quote_context},
                metadata=metadata,
                simulations=count,
                seed=simulation_seed,
            )
        )

    actual_simulations = simulations
    report = execute(actual_simulations)
    output = report.markets[0]

    if spec.side == "over":
        raw_p_win = output.p_over
        raw_p_loss = output.p_under
    else:
        raw_p_win = output.p_under
        raw_p_loss = output.p_over

    offered_decimal = american_to_decimal(offered_american)
    raw_expected_roi = (
        raw_p_win * (offered_decimal - 1)
        - raw_p_loss
    )
    adaptive_escalation = should_escalate_simulations(
        requested_simulations=actual_simulations,
        monte_carlo_error=output.monte_carlo_error,
        expected_roi=raw_expected_roi,
    )

    if adaptive_escalation:
        actual_simulations = 100_000
        report = execute(actual_simulations)
        output = report.markets[0]
        if spec.side == "over":
            raw_p_win = output.p_over
            raw_p_loss = output.p_under
        else:
            raw_p_win = output.p_under
            raw_p_loss = output.p_over
        raw_expected_roi = (
            raw_p_win * (offered_decimal - 1)
            - raw_p_loss
        )

    non_push = max(1.0 - output.p_push, 1e-12)
    raw_conditional_win = raw_p_win / non_push
    original_line = market.get("line")
    exact_line = (
        line is None
        or (
            original_line is not None
            and math.isclose(
                float(line),
                float(original_line),
                abs_tol=1e-9,
            )
        )
    )
    requested_side = str(
        side or market.get("side") or ""
    ).lower()
    original_side = str(market.get("side") or "").lower()
    anchor_eligible = exact_line and requested_side == original_side

    elapsed_fraction = _clip(
        _elapsed_seconds(game) / 2400.0,
        0.0,
        1.0,
    )
    consensus = consensus_for_market(
        market_feed,
        market,
        elapsed_fraction=elapsed_fraction,
    )

    consensus_applied = (
        anchor_eligible
        and consensus.probability is not None
        and consensus.book_count >= 2
    )

    if consensus_applied:
        conditional_win = blend_conditional_probability(
            raw_conditional_win,
            consensus.probability,
            consensus.weight,
        )
        p_win = conditional_win * non_push
        p_loss = (1.0 - conditional_win) * non_push
    else:
        conditional_win = raw_conditional_win
        p_win = raw_p_win
        p_loss = raw_p_loss

    fair_decimal = (
        math.inf
        if p_win <= 0
        else non_push / p_win
    )
    expected_roi = p_win * (offered_decimal - 1) - p_loss

    raw_model_uncertainty = math.sqrt(
        output.monte_carlo_error**2
        + profile_uncertainty**2
    )
    if consensus_applied and consensus.uncertainty is not None:
        total_uncertainty = max(
            0.020,
            math.sqrt(
                (1.0 - consensus.weight) ** 2
                * raw_model_uncertainty**2
                + consensus.weight**2
                * consensus.uncertainty**2
            ),
        )
    else:
        total_uncertainty = raw_model_uncertainty

    allowance = 1.645 * total_uncertainty
    conservative_win = max(0.0, p_win - allowance)
    conservative_loss = min(
        non_push,
        p_loss + allowance,
    )
    conservative_roi = (
        conservative_win * (offered_decimal - 1)
        - conservative_loss
    )

"""
        text = text[:run_start] + new_run_block + text[return_start:]

    replacements = {
        '"simulation_count": simulations,':
            '"simulation_count": actual_simulations,\n'
            '        "adaptive_simulation_escalation": adaptive_escalation,',
        '"calibration_status": "NOT_OOS_CALIBRATED",':
            '"calibration_status": (\n'
            '            "MARKET_ANCHORED_NOT_OOS_CALIBRATED"\n'
            '            if consensus_applied\n'
            '            else "NOT_OOS_CALIBRATED"\n'
            '        ),',
        '"official_recommendation": False,':
            '"official_recommendation": False,\n'
            '        "raw_model_win_probability": raw_p_win,\n'
            '        "raw_model_expected_roi": raw_expected_roi,\n'
            '        "market_consensus_probability": consensus.probability,\n'
            '        "market_consensus_book_count": consensus.book_count,\n'
            '        "market_consensus_dispersion": consensus.dispersion,\n'
            '        "market_consensus_weight": (\n'
            '            consensus.weight if consensus_applied else 0.0\n'
            '        ),\n'
            '        "market_consensus_applied": consensus_applied,\n'
            '        "price_age_seconds": price_age_seconds,\n'
            '        "state_age_seconds": state_age_seconds,\n'
            '        "simulation_seed": simulation_seed,\n'
            '        "confidence": confidence_label(total_uncertainty),',
        '"This result uses the integrated possession-based reference "':
            '"This result uses the integrated possession-based simulator, "\n'
            '            "deterministic common random numbers, adaptive Monte Carlo, "\n'
            '            "strict freshness gates, and a robust no-vig consensus "\n'
            '            "anchor when comparable books are available. "',
        '"simulator with live game state and Bayesian-shrunk live profiles. "\n'
        '            "It is not an official published recommendation until WNBA-specific "':
            '"It is not an official published recommendation until WNBA-specific "',
    }
    for old, new in replacements.items():
        if old in text:
            text = text.replace(old, new, 1)

    LIVE_REFERENCE.write_text(text, encoding="utf-8")


def patch_env_example() -> None:
    if not ENV_EXAMPLE.exists():
        return
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    replacements = {
        "HIGH_LIABILITY_SIMULATIONS=50000":
            "HIGH_LIABILITY_SIMULATIONS=100000",
        "MIN_BOOK_COUNT=2": "MIN_BOOK_COUNT=3",
        "MAX_TOTAL_UNCERTAINTY=0.08":
            "MAX_TOTAL_UNCERTAINTY=0.06",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    ENV_EXAMPLE.write_text(text, encoding="utf-8")


def main() -> None:
    patch_live_reference()
    patch_env_example()
    print("RUNTIME OPTIMIZATION V1 APPLIED")


if __name__ == "__main__":
    main()
