from __future__ import annotations

import math

from wizard_wnba.calibration import (
    BetaCalibrator,
    HierarchicalBetaCalibrator,
    fit_beta_coefficients,
)


def _samples(rate, n, p=0.5):
    ones = round(rate * n)
    return [(p, 1 if i < ones else 0, 1.0) for i in range(n)]


def test_credibility_weight_formula():
    cal = HierarchicalBetaCalibrator(
        pooled=(1.0, -1.0, 0.0),
        own={"thin": (2.0, -2.0, 1.0), "rich": (0.5, -0.5, -0.5)},
        sizes={"thin": 40, "rich": 4000},
        k=100.0,
    )
    assert math.isclose(cal.credibility("thin"), 40 / 140)
    assert math.isclose(cal.credibility("rich"), 4000 / 4100)


def test_thin_market_shrinks_toward_pooled():
    cal = HierarchicalBetaCalibrator(
        pooled=(1.0, -1.0, 0.0), own={"thin": (3.0, -3.0, 2.0)},
        sizes={"thin": 10}, k=1000.0,
    )
    a, b, c = cal.shrunk_coefficients("thin")
    # Very low credibility (10/1010) -> essentially the pooled coefficients.
    assert abs(a - 1.0) < 0.05 and abs(b - (-1.0)) < 0.05 and abs(c) < 0.05


def test_rich_market_keeps_own_fit():
    cal = HierarchicalBetaCalibrator(
        pooled=(1.0, -1.0, 0.0), own={"rich": (0.4, -0.6, -0.3)},
        sizes={"rich": 100000}, k=100.0,
    )
    a, b, c = cal.shrunk_coefficients("rich")
    assert abs(a - 0.4) < 0.01 and abs(b - (-0.6)) < 0.01 and abs(c - (-0.3)) < 0.01


def test_k_zero_is_full_own_credibility():
    cal = HierarchicalBetaCalibrator(
        pooled=(1.0, -1.0, 0.0), own={"m": (0.7, -0.8, 0.2)},
        sizes={"m": 50}, k=0.0,
    )
    assert cal.shrunk_coefficients("m") == (0.7, -0.8, 0.2)


def test_fit_produces_pooled_and_per_market():
    by_market = {
        "a": _samples(0.5, 400, p=0.5),
        "b": _samples(0.5, 400, p=0.5),
    }
    model = HierarchicalBetaCalibrator.fit(by_market, k=50.0)
    assert set(model.own) == {"a", "b"}
    assert model.sizes["a"] == 400
    params = model.bundle_parameters()
    assert set(params) == {"a", "b"}
    assert all(len(v) == 3 for v in params.values())


def test_shrinkage_beats_overfit_on_validation():
    # A thin market whose OWN fit overfits (all wins) but whose true rate is
    # 0.5; the pool is well-calibrated. Shrinkage (k>0) must lower validation
    # log-loss versus k=0 (pure own fit).
    fit = {
        "rich": [(p, 1 if i % 2 == 0 else 0, 1.0)
                 for p in (0.3, 0.5, 0.7) for i in range(400)],
        "thin": [(0.5, 1, 1.0) for _ in range(40)],   # degenerate: all wins
    }
    val = {"thin": _samples(0.5, 400, p=0.5)}         # true rate 0.5
    best_k, scores = HierarchicalBetaCalibrator.tune_k(fit, val)
    assert scores[0.0] > min(scores[k] for k in scores if k > 0)
    assert best_k > 0


def test_calibrator_for_returns_beta_calibrator():
    cal = HierarchicalBetaCalibrator(
        pooled=(1.0, -1.0, 0.0), own={"m": (1.0, -1.0, 0.0)},
        sizes={"m": 500}, k=100.0,
    )
    bc = cal.calibrator_for("m")
    assert isinstance(bc, BetaCalibrator)
    # identity-ish calibrator preserves probability
    assert abs(bc.transform(0.4) - 0.4) < 0.05


def test_fit_beta_coefficients_identity_on_calibrated_data():
    # Data already calibrated at each level -> fit stays near identity (1,-1,0).
    samples = [(p, 1 if i < round(p * 300) else 0, 1.0)
               for p in (0.2, 0.5, 0.8) for i in range(300)]
    a, b, c = fit_beta_coefficients(samples)
    assert abs(a - 1.0) < 0.25 and abs(b + 1.0) < 0.25 and abs(c) < 0.25
