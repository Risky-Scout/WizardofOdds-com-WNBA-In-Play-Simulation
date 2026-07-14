# Security

## Secrets

- provider keys live only in the runtime secret file;
- `.env` is excluded from Git;
- file mode is `0600`;
- keys are passed as environment variables, not command-line parameters;
- logs must not contain request URLs with The Odds API key;
- rotate any key that appears in a transcript or log.

## Containers

Production containers:

- run as a non-root user;
- use a read-only root filesystem;
- enable `no-new-privileges`;
- expose the API only on loopback;
- persist only `/data`;
- restart automatically.

## GitHub

- use a private repository initially;
- protect `main`;
- require CI;
- require production environment approval;
- use an immutable commit-SHA image;
- restrict deployment secrets to the production environment;
- review dependency updates.

## Public API

Before broad public exposure, add at the reverse proxy:

- request rate limits;
- bot controls;
- cache rules for non-live endpoints;
- strict transport security;
- origin and host validation;
- access logs without provider secrets.

## Data licensing

Confirm that all provider terms permit:

- storage;
- transformation;
- public display;
- historical analysis;
- bookmaker deep links;
- commercial WizardofOdds.com use.
