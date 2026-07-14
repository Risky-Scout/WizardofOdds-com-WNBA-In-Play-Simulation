# User experience

## Principles

The dashboard prioritizes decision quality over pick volume.

A strong bettor-facing interface must make these distinctions visible:

- model probability versus sportsbook implied probability;
- expected ROI versus conservative ROI;
- point estimate versus uncertainty;
- fair odds versus available odds;
- current recommendation versus expired recommendation.

## Current interface

The included responsive dashboard provides:

- live connection and update state;
- game strip with score, period, clock, and event sequence;
- Published, Qualified, Watch, and All tabs;
- market and grade filters;
- exact book, line, and price;
- model probability;
- fair odds;
- expected and conservative ROI;
- adaptive required ROI;
- uncertainty and market age;
- distribution visualization;
- methodology and responsible-use explanation;
- WebSocket updates with HTTP fallback.

## Production integration

Use the existing WizardofOdds.com header, navigation, analytics, consent, and
accessibility components around the application.

Do not remove:

- last-updated time;
- market age;
- status;
- uncertainty;
- responsible-use language;
- recommendation expiry behavior.

## Language

Use:

- model estimate;
- fair probability;
- expected ROI;
- conservative ROI;
- published opportunity;
- withdrawn;
- suspended.

Avoid:

- lock;
- guaranteed;
- sure thing;
- risk-free;
- can't lose.

## Performance record

The public record should show all published recommendations, including losses
and withdrawals, using the originally captured line and price.
