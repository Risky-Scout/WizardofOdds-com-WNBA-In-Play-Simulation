from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from wizard_wnba.oos_validation import (
    ValidationThresholds,
    evaluate_oos_rows,
    load_jsonl,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the WNBA simulator's strict out-of-sample promotion gate."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/calibration/oos_gate.json"),
    )
    parser.add_argument(
        "--promotion-marker",
        type=Path,
        default=Path("data/calibration/PROMOTION_APPROVED"),
    )
    parser.add_argument("--minimum-rows", type=int, default=1000)
    parser.add_argument("--minimum-selected-bets", type=int, default=200)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    rows = load_jsonl(args.dataset)
    metrics = evaluate_oos_rows(
        rows,
        thresholds=ValidationThresholds(
            minimum_rows=args.minimum_rows,
            minimum_selected_bets=args.minimum_selected_bets,
            bootstrap_samples=args.bootstrap_samples,
        ),
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(metrics.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    args.promotion_marker.parent.mkdir(parents=True, exist_ok=True)
    if metrics.passed:
        args.promotion_marker.write_text(
            "approved\n",
            encoding="utf-8",
        )
    elif args.promotion_marker.exists():
        args.promotion_marker.unlink()

    print(json.dumps(metrics.to_dict(), indent=2, sort_keys=True))
    return 0 if metrics.passed else 2


if __name__ == "__main__":
    sys.exit(main())
