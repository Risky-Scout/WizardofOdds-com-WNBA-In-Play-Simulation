from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from wizard_wnba.historical_walkforward import (
    HistoricalConfig,
    HistoricalPipelineError,
    HistoricalWalkForwardPipeline,
    load_env,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill historical WNBA games, play-by-play, player stats, "
            "and sportsbook snapshots; run chronological walk-forward "
            "calibration; and promote only when the strict OOS gate passes."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "/data")),
    )
    parser.add_argument(
        "--seasons",
        default="2024,2025,2026",
        help="Comma-separated seasons. The latest season is untouched OOS.",
    )
    parser.add_argument(
        "--historical-simulations",
        type=int,
        default=1000,
    )
    parser.add_argument(
        "--max-games",
        type=int,
        default=0,
        help="0 means all completed games in the requested seasons.",
    )
    parser.add_argument("--minimum-rows", type=int, default=1000)
    parser.add_argument("--minimum-selected-bets", type=int, default=200)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument(
        "--request-pause-seconds",
        type=float,
        default=0.12,
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env(args.env_file)
    seasons = tuple(
        int(value.strip())
        for value in args.seasons.split(",")
        if value.strip()
    )
    if len(seasons) < 2:
        print("ERROR: at least two seasons are required", file=sys.stderr)
        return 2
    config = HistoricalConfig(
        data_dir=args.data_dir,
        seasons=seasons,
        historical_simulations=args.historical_simulations,
        max_games=args.max_games,
        minimum_rows=args.minimum_rows,
        minimum_selected_bets=args.minimum_selected_bets,
        bootstrap_samples=args.bootstrap_samples,
        request_pause_seconds=args.request_pause_seconds,
    )
    pipeline = HistoricalWalkForwardPipeline(config)
    try:
        report = pipeline.run()
    except (HistoricalPipelineError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    finally:
        pipeline.close()

    metrics = report["oos_metrics"]
    print()
    print("===== WALK-FORWARD RESULT =====")
    print("calibration_slope:", metrics["calibration_slope"])
    print("calibration_intercept:", metrics["calibration_intercept"])
    print("oos_brier:", metrics["oos_brier"])
    print("consensus_brier:", metrics["consensus_brier"])
    print("after_vig_roi:", metrics["after_vig_roi"])
    print("bootstrap_roi_lower_95:", metrics["bootstrap_roi_lower_95"])
    print("stale_residual_bias:", metrics["stale_residual_bias"])
    print("availability_residual_bias:", metrics["availability_residual_bias"])
    print("promotion_passed:", metrics["passed"])
    print("blockers:", metrics["blockers"])

    return 0 if metrics["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
