# Release Verification

Repository title: WizardofOdds.com WNBA In-Play Simulation  
Recommended GitHub slug: `WizardofOdds-com-WNBA-In-Play-Simulation`

## Results

- Full test suite: [32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m                                                      [100%][0m
- Bytecode compilation: PASS
- Python package wheel: PASS
- API/dashboard smoke test: PASS
- Static unused-import check: PASS
- Forbidden public CLV fields in generated output: 0
- Adaptive policy: enabled
- Hard minimum conservative ROI: 2.00%
- Demo recommendations generated: 24
- Demo recommendations published: 9
- Local Git HEAD: `90ccd67804fcd1ded717ca43cf00fd5d833b1b41`
- Working tree clean: YES

## Runtime boundary

GitHub is configured for source control, CI, container builds, and protected
deployment. The live process is designed for the persistent `sportsodds` host.

## Production blockers intentionally enforced

The live worker publishes zero recommendations until:

1. BALLDONTLIE and The Odds API keys are installed as runtime secrets.
2. `/data/models/production.json` exists and validates.
3. Every active player has a model profile and canonical identity.
4. Required market calibrators exist.
5. Current game and market data pass freshness and reconciliation gates.

## Environment limitation

A local Docker daemon was not available in this build environment, so the
container image was not executed here. The Dockerfile was packaged, the Python
wheel built successfully, the API ran through FastAPI's test client, and GitHub
CI includes an actual Docker build and health-check step.
