.PHONY: install test compile demo up down validate-model

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

compile:
	python -m compileall -q src

demo:
	DATA_DIR=./data LIVE_ENABLED=false wizard-wnba-worker --mode demo

up:
	docker compose up --build -d

down:
	docker compose down

validate-model:
	PYTHONPATH=src python scripts/validate_model_bundle.py data/models/production.json
