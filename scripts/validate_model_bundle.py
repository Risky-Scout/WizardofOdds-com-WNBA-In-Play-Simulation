from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from wizard_wnba.model_bundle import ModelBundle, ModelBundleError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    try:
        bundle = ModelBundle.load(args.path)
    except ModelBundleError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1

    report = bundle.validation_report
    blockers = []
    if int(report["sample_size"]) < 1000:
        blockers.append("sample_size_below_1000")
    slope = float(report["calibration_slope"])
    if not 0.85 <= slope <= 1.15:
        blockers.append("calibration_slope_outside_0.85_1.15")
    if float(report["oos_brier"]) >= 0.25:
        blockers.append("oos_brier_not_better_than_uninformed_binary_baseline")

    print(
        json.dumps(
            {
                "valid": not blockers,
                "model_version": bundle.metadata.model_version,
                "calibrator_id": bundle.metadata.calibrator_id,
                "profile_count": len(bundle.profiles),
                "trained_through": bundle.trained_through,
                "validation_report": report,
                "blockers": blockers,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
