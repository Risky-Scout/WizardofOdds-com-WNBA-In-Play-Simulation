from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import ReferenceAPI
from .demo import build_demo_request
from .service import SimulationService
from .validation import validate_report_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wnba-inplay",
        description="WNBA in-play simulation reference CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo")
    demo.add_argument("--simulations", type=int, default=250)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--output", type=Path)

    validate = subparsers.add_parser("validate-report")
    validate.add_argument("path", type=Path)

    subparsers.add_parser("health")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "demo":
        report = SimulationService().run(
            build_demo_request(
                simulations=args.simulations,
                seed=args.seed,
            )
        )
        payload = report.to_dict()
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0 if report.manifest.status == "SUCCESS" else 1

    if args.command == "validate-report":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        result = validate_report_dict(payload)
        print(
            json.dumps(
                {
                    "valid": result.valid,
                    "reasons": result.reasons,
                    "duplicate_pmfs": result.duplicate_pmfs,
                    "invalid_pmfs": result.invalid_pmfs,
                    "duplicate_market_rows":
                        result.duplicate_market_rows,
                    "stale_artifacts": result.stale_artifacts,
                },
                indent=2,
            )
        )
        return 0 if result.valid else 1

    if args.command == "health":
        status, _, body = ReferenceAPI().handle("GET", "/health")
        print(body.decode("utf-8"))
        return 0 if status == 200 else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
