from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from .domain import PlayerRateProfile
from .pipeline import ModelMetadata


class ModelBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelBundle:
    metadata: ModelMetadata
    profiles: Mapping[str, PlayerRateProfile]
    trained_through: str
    validation_report: Mapping[str, float | int | str]

    @classmethod
    def load(cls, path: Path) -> "ModelBundle":
        if not path.exists():
            raise ModelBundleError(f"model bundle not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata_payload = dict(payload["metadata"])
        metadata_payload["calibration_parameters"] = {
            key: tuple(value)
            for key, value in metadata_payload.get(
                "calibration_parameters", {}
            ).items()
        }
        metadata = ModelMetadata(**metadata_payload)
        profiles = {
            item["canonical_player_id"]: PlayerRateProfile(**item)
            for item in payload["profiles"]
        }
        validation = payload.get("validation_report", {})
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
        if metadata.calibration_score < 0.75:
            raise ModelBundleError("model calibration score is below production gate")
        return cls(
            metadata=metadata,
            profiles=profiles,
            trained_through=str(payload["trained_through"]),
            validation_report=validation,
        )
