from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .domain import PlayerRateProfile
from .pipeline import POSSESSION_PROBABILITY_SOURCE, ModelMetadata


# Production market policy. Only these markets may ship a calibrator and be
# priced live. player_assists and player_points_rebounds_assists are excluded.
PRODUCTION_PERMITTED_MARKETS = frozenset(
    {
        "h2h",
        "player_points",
        "player_rebounds",
        "player_threes",
        "spreads",
        "totals",
    }
)


class ModelBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelBundle:
    metadata: ModelMetadata
    profiles: Mapping[str, PlayerRateProfile]
    trained_through: str
    validation_report: Mapping[str, float | int | str]
    model_hash: str

    @property
    def eligible_markets(self) -> frozenset[str]:
        return self.metadata.eligible_markets

    @property
    def probability_source(self) -> str:
        return self.metadata.probability_source

    @property
    def model_version(self) -> str:
        return self.metadata.model_version

    @property
    def per_market_validation(self) -> dict[str, bool]:
        """Per-market OOS gate outcome, {market: passed}. Empty when the bundle
        carries no per-market gate (older bundles) — callers then treat every
        market as not-yet-validated, so no market shows a validated badge it
        did not earn."""
        gate = self.validation_report.get("per_market_gate")
        if not isinstance(gate, dict):
            return {}
        out: dict[str, bool] = {}
        for market, result in gate.items():
            if isinstance(result, dict):
                out[str(market)] = bool(result.get("passed", False))
        return out

    @classmethod
    def load(cls, path: Path) -> "ModelBundle":
        if not path.exists():
            raise ModelBundleError(f"model bundle not found: {path}")
        raw = path.read_text(encoding="utf-8")
        model_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        payload = json.loads(raw)
        if "metadata" not in payload or "profiles" not in payload:
            raise ModelBundleError(
                "model bundle is missing required 'metadata'/'profiles' sections"
            )
        metadata_payload = dict(payload["metadata"])
        metadata_payload["calibration_parameters"] = {
            key: tuple(float(component) for component in value)
            for key, value in metadata_payload.get(
                "calibration_parameters", {}
            ).items()
        }

        validation = payload.get("validation_report", {})
        # The promoted probability source lives in the validation report; carry
        # it onto the metadata so the pricing path can verify a source match.
        probability_source = str(
            metadata_payload.get("probability_source")
            or validation.get("probability_source")
            or ""
        )
        if probability_source:
            metadata_payload["probability_source"] = probability_source
        else:
            metadata_payload.setdefault(
                "probability_source", POSSESSION_PROBABILITY_SOURCE
            )
        # Drop any keys ModelMetadata does not accept (validation-only fields).
        metadata_payload = {
            key: value
            for key, value in metadata_payload.items()
            if key
            in {
                "model_version",
                "calibrator_id",
                "calibration_score",
                "calibration_se",
                "model_se",
                "calibration_parameters",
                "probability_source",
            }
        }
        metadata = ModelMetadata(**metadata_payload)

        profiles = {
            item["canonical_player_id"]: PlayerRateProfile(**item)
            for item in payload["profiles"]
        }
        required = {
            "oos_log_loss",
            "oos_brier",
            "calibration_slope",
            "sample_size",
        }
        missing = required - set(validation)
        if missing:
            raise ModelBundleError(
                f"model bundle validation report is missing: {sorted(missing)}"
            )
        if not metadata.calibration_parameters:
            raise ModelBundleError("model bundle has no calibrators")

        forbidden = set(metadata.eligible_markets) - PRODUCTION_PERMITTED_MARKETS
        if forbidden:
            raise ModelBundleError(
                "model bundle ships calibrators for non-permitted markets: "
                f"{sorted(forbidden)}"
            )

        # Fail production publication when the runtime probability source does
        # not match the possession-raw source the calibrators were trained on.
        if metadata.probability_source != POSSESSION_PROBABILITY_SOURCE:
            raise ModelBundleError(
                "model bundle probability_source "
                f"'{metadata.probability_source}' does not match required "
                f"'{POSSESSION_PROBABILITY_SOURCE}'"
            )
        if metadata.calibration_score < 0.75:
            raise ModelBundleError("model calibration score is below production gate")
        return cls(
            metadata=metadata,
            profiles=profiles,
            trained_through=str(payload["trained_through"]),
            validation_report=validation,
            model_hash=model_hash,
        )


def load_per_market_validation(data_dir: Path) -> dict[str, bool]:
    """Best-effort {market: validated} from the production bundle. Returns {}
    (all markets not-validated) on any load/parse failure — safe by default."""
    path = data_dir / "models" / "production.json"
    try:
        return ModelBundle.load(path).per_market_validation
    except Exception:
        return {}
