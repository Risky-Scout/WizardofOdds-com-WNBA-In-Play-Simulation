from __future__ import annotations

import json

import pytest

from wizard_wnba.model_bundle import ModelBundle, ModelBundleError
from wizard_wnba.storage import RawSnapshotStore


def test_raw_snapshot_store_is_content_addressed_and_append_only(tmp_path):
    store = RawSnapshotStore(tmp_path / "raw")
    first = store.save(
        provider="provider",
        endpoint="/endpoint",
        payload={"value": 1},
    )
    second = store.save(
        provider="provider",
        endpoint="/endpoint",
        payload={"value": 2},
    )
    assert first.path.exists()
    assert second.path.exists()
    assert first.sha256 != second.sha256
    assert first.path != second.path


def test_model_bundle_requires_calibrators(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "model_version": "v1",
                    "calibrator_id": "none",
                    "calibration_score": .9,
                    "calibration_se": .01,
                    "model_se": .02,
                    "calibration_parameters": {}
                },
                "trained_through": "2026-01-01",
                "validation_report": {
                    "oos_log_loss": .6,
                    "oos_brier": .2,
                    "calibration_slope": 1,
                    "sample_size": 1000
                },
                "profiles": []
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModelBundleError, match="no calibrators"):
        ModelBundle.load(path)
