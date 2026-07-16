#!/usr/bin/env python3
"""Re-validate a model bundle per market and write honest gate labels.

Runs the strict per-market gate (oos_validation.evaluate_by_market) on a
bundle's real out-of-sample predictions and injects the result into the
bundle's validation_report under ``per_market_gate``. It does NOT change any
calibrator — a market that fails its gate keeps the current calibrator and is
simply labeled honestly. Only markets that pass the per-market gate on real OOS
data are marked ``passed: true``.

Usage:
    python scripts/revalidate_bundle.py IN_BUNDLE OOS_PREDICTIONS.jsonl OUT_BUNDLE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wizard_wnba.oos_validation import (  # noqa: E402
    PerMarketGate,
    evaluate_by_market,
    prepare_oos_rows,
)


def revalidate(bundle_path: Path, oos_path: Path) -> dict:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    rows = []
    with oos_path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                if r.get("outcome") is None and r.get("binary_outcome") in (0, 1):
                    r["outcome"] = int(r["binary_outcome"])
                rows.append(r)

    prepared = prepare_oos_rows(rows)
    results = evaluate_by_market(prepared, gate=PerMarketGate())
    per_market = {m: res.to_dict() for m, res in results.items()}

    vr = bundle.setdefault("validation_report", {})
    vr["per_market_gate"] = per_market
    vr["per_market_gate_thresholds"] = {
        "minimum_selected_bets": PerMarketGate.minimum_selected_bets,
        "minimum_bootstrap_lower_roi": PerMarketGate.minimum_bootstrap_lower_roi,
        "calibration_slope_min": PerMarketGate.calibration_slope_min,
        "calibration_slope_max": PerMarketGate.calibration_slope_max,
        "note": "STRICTER than the pooled gate: each market must independently "
                "clear >=100 selected bets, positive bootstrap L95, slope in "
                "[0.85,1.15], and no material stale/availability bias.",
    }
    return bundle


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    in_bundle, oos, out_bundle = map(Path, sys.argv[1:4])
    bundle = revalidate(in_bundle, oos)
    out_bundle.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pm = bundle["validation_report"]["per_market_gate"]
    print(f"wrote {out_bundle}")
    print(f"{'market':16}{'sel':>5}{'slope':>8}{'bootL95':>9}{'PASS':>6}")
    validated = []
    for m, r in pm.items():
        s = r["calibration_slope"]
        b = r["bootstrap_roi_lower_95"]
        print(f"{m:16}{r['selected_bets']:5}"
              f"{(f'{s:.3f}' if s is not None else 'None'):>8}"
              f"{(f'{b:.3f}' if b is not None else 'None'):>9}"
              f"{str(r['passed']):>6}")
        if r["passed"]:
            validated.append(m)
    print(f"VALIDATED markets: {validated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
