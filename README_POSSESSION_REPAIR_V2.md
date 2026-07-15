# WNBA Walk-Forward Possession Repair v2

This repair uses the cached 755-game replay and does not redownload or resimulate it.

It fixes the remaining root causes:

1. Evaluates and calibrates `possession_raw_probability`, the actual possession simulator used by Scenario Lab.
2. Uses a chronological two-stage calibration: early seasons for base calibration, later validation games for post-calibration, latest season untouched for OOS.
3. Excludes unavailable, stale, roster-proxy, misaligned, and missing-age rows from the actionable population.
4. Evaluates stale/availability integrity by zero contamination in selected wagers rather than comparing structurally different excluded populations.
5. Allows publication only for markets that demonstrate signal on the validation period.
6. Selects at most one wager family per game at the first qualifying checkpoint, avoiding hindsight selection across later prices.
7. Recomputes consensus Brier, after-vig ROI, and game-cluster bootstrap on the corrected OOS population.
8. Preserves all current data before repair.

A promotion marker is created only if every corrected OOS gate passes.
