.PHONY: sync lint fmt test run run-small tail clean langfuse-up langfuse-down langfuse-smoke

QUESTION ?= Will the Strait of Hormuz sustain at least 100 ship transits per day on a 7-day moving average by 2026-06-30?
LANGFUSE_DIR ?= deploy/langfuse

sync:
	uv sync

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

test:
	uv run pytest

# Full live dynamic forecast run.
run:
	uv run dynamic-graph run --question "$(QUESTION)"

# Tiny live run with tight caps (acceptance smoke).
run-small:
	uv run dynamic-graph run --question "$(QUESTION)" --small

# Tail a run's local event stream: make tail RUN_ID=<id>
tail:
	uv run dynamic-graph tail --run-id "$(RUN_ID)"

# Local Langfuse stack (headless-provisioned keys; see deploy/langfuse/.env).
langfuse-up:
	cd $(LANGFUSE_DIR) && docker compose up -d

langfuse-down:
	cd $(LANGFUSE_DIR) && docker compose down

# Populate Langfuse with one real trace using offline fakes (no API keys needed).
langfuse-smoke:
	uv run python scripts/langfuse_smoke.py

clean:
	rm -rf runs
