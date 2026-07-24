
VENV := .venv
PY := $(VENV)/bin/python
PIP := uv pip
SHIPPING_PORT := 8900

.PHONY: help install signoz seed setup shipping traffic dashboards demo test lint clean

help:
	@echo "make setup       - start SigNoz, install deps, seed the DB"
	@echo "make shipping    - run the mock shipping service (foreground)"
	@echo "make traffic     - generate ~30 agent sessions into SigNoz"
	@echo "make dashboards  - import dashboards/*.json into SigNoz"
	@echo "make demo        - scripted incident -> replay -> diff (M5)"
	@echo "make test / lint - unit tests / ruff"

install:
	uv venv --python 3.11
	$(PIP) install -e ".[dev]"

signoz:
	cd deploy && foundryctl cast -f casting.yaml

seed:
	$(PY) -m support_agent.db

setup: install signoz seed
	@echo "\nSigNoz UI: http://localhost:8080"
	@echo "Next: 'make shipping' (in another shell), then 'make traffic'."

shipping:
	$(VENV)/bin/uvicorn support_agent.shipping_service:app --port $(SHIPPING_PORT)

traffic:
	$(PY) scripts/generate_traffic.py -n 30

dashboards:
	$(PY) scripts/import_dashboards.py

demo:
	$(PY) scripts/demo.py

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check rewind support_agent scripts

clean:
	rm -f support_agent/data/*.db
