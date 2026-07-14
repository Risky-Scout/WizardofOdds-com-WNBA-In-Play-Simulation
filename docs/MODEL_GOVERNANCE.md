# Model governance

## Selection objective

Maximize out-of-sample expected logarithmic bankroll growth or expected ROI,
subject to hard probability-quality and operational constraints.

ROI may choose among models that already pass calibration. It may not compensate
for failed calibration.

## Required validation

For every promoted model:

- train/validation/test splits are chronological;
- no game appears in more than one split;
- all features are reproducible as of prediction time;
- market and player segments have sufficient sample size;
- log loss and Brier score are reported;
- calibration intercept and slope are reported;
- reliability curves are retained;
- recommendation ROI includes uncertainty intervals;
- drawdown and false-positive rate are reported;
- performance includes an execution-delay sensitivity analysis.

## Model bundle contract

`production.json` contains:

```text
metadata
  model_version
  calibrator_id
  calibration_score
  calibration_se
  model_se
  calibration_parameters by market

trained_through
validation_report
profiles by canonical player ID
```

The worker fails closed if:

- the bundle is absent;
- calibrators are absent;
- calibration is below the gate;
- an active player profile is absent;
- required validation fields are absent.

## Calibration

The included registry applies beta calibration to conditional win probability
while preserving exact integer-line push mass.

Calibrators must be fitted using predictions not used to fit the underlying
model.

## Champion/challenger

- one signed champion serves public recommendations;
- challengers run in shadow mode;
- all candidates use identical replay inputs;
- promotion is versioned and reversible;
- the previous bundle remains available for rollback.

## True closing-line value

CLV is evaluated only after a later timestamped market observation exists. A
current model-market difference is an edge, not CLV.
