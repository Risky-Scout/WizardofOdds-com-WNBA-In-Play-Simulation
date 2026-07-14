# Go-live checklist

## Infrastructure

- [ ] Private GitHub repository created.
- [ ] `main` branch protected.
- [ ] CI required before merge.
- [ ] GHCR image build successful.
- [ ] `production` GitHub environment protected.
- [ ] sportsodds has Docker Compose v2.
- [ ] `/opt/wizard-wnba/data` is durable and monitored.
- [ ] Reverse proxy supports HTTPS and WebSocket upgrade.
- [ ] Host clock synchronization verified.

## Provider access

- [ ] BALLDONTLIE production key installed on sportsodds.
- [ ] BALLDONTLIE endpoints contract-tested.
- [ ] The Odds API production key installed on sportsodds.
- [ ] Historical-data plan and quota confirmed.
- [ ] Odds quota alerts configured.
- [ ] Data-provider display and storage rights confirmed.

## Model

- [ ] Historical event/state dataset built without future leakage.
- [ ] Historical prop snapshots archived.
- [ ] Remaining-minutes model trained.
- [ ] Player rate and game outcome models trained.
- [ ] Market-specific calibrators fitted out of sample.
- [ ] Every active player has a canonical ID and profile.
- [ ] `production.json` validates.
- [ ] Replay results reviewed by market and game state.
- [ ] Prospective shadow period completed.
- [ ] Adaptive threshold and 2% hard floor frozen.

## Publication integrity

- [ ] Current-cycle-only snapshot verified.
- [ ] Missing model bundle produces zero live recommendations.
- [ ] Missing player mapping produces zero affected recommendations.
- [ ] Stale game state suspends.
- [ ] Stale price suspends.
- [ ] Missing calibrator fails closed.
- [ ] Integer-line push probabilities match PMF mass.
- [ ] Recommendation expiration works.
- [ ] Price changes generate a new recommendation identity.
- [ ] Public history retains losses and withdrawals.
- [ ] No current model edge is labeled CLV.

## UX

- [ ] Existing WizardofOdds.com navigation applied.
- [ ] Mobile layout reviewed.
- [ ] Screen-reader labels reviewed.
- [ ] Live/reconnecting state is visible.
- [ ] Last-updated time is visible.
- [ ] Exact book, line, and price are visible.
- [ ] Expected and conservative ROI are distinguished.
- [ ] Responsible-use wording approved.
- [ ] Analytics do not expose provider secrets.

## Launch progression

- [ ] Historical replay.
- [ ] Live data archive only.
- [ ] Live shadow recommendations.
- [ ] Internal-only recommendations.
- [ ] Limited public beta.
- [ ] Full automated publication.
