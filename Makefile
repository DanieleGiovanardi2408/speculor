# Radar: common commands. `make` or `make help` lists them.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
PNPM ?= pnpm
API_PORT ?= 8000

# Demo mode: throwaway SQLite DB + offline fake LLM. No API keys, no network for scoring.
DEMO_ENV := RADAR_DATABASE_URL=sqlite:///$(CURDIR)/data/demo.db RADAR_LLM_PROVIDER=fake RADAR_DRAFT_PROVIDER=fake
# Real local mode: data/radar.db (the default in radar.config); LLM keys come from .env.
REAL_ENV := RADAR_DATABASE_URL=sqlite:///$(CURDIR)/data/radar.db

# Runs the API and the Vite dev server together; Ctrl+C stops both.
define serve_both
	trap 'kill $$(jobs -p) 2>/dev/null; wait' INT TERM EXIT; \
	$(1) $(UV) run radar serve --port $(API_PORT) & \
	$(PNPM) -C frontend dev & \
	wait
endef

.PHONY: help install dev dev-real seed test lint fmt run-pipeline report send send-dry build \
	schedule-install schedule-remove schedule-status schedule-run-now clean

help: ## List available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install backend (uv) and frontend (pnpm) deps, enable pre-commit hooks
	$(UV) sync
	$(PNPM) -C frontend install
	$(UV) run pre-commit install

dev: ## Local demo (fictional data, no API keys): API on :8000 + web app on :5173
	rm -f data/demo.db
	$(DEMO_ENV) $(UV) run radar init-db
	$(DEMO_ENV) $(UV) run radar demo
	@echo "Open http://localhost:5173  (Ctrl+C to stop)"
	@$(call serve_both,$(DEMO_ENV))

dev-real: ## Local app on your real data (data/radar.db)
	$(REAL_ENV) $(UV) run radar init-db
	@echo "Open http://localhost:5173  (Ctrl+C to stop)"
	@$(call serve_both,$(REAL_ENV))

seed: ## Import the manual tracker export (private/radar_seed.json) into data/radar.db
	$(REAL_ENV) $(UV) run radar init-db
	$(REAL_ENV) $(UV) run radar seed

test: ## Run backend and frontend tests
	$(UV) run pytest
	$(PNPM) -C frontend test

lint: ## Lint + format check + type check (backend and frontend)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy
	$(PNPM) -C frontend lint
	$(PNPM) -C frontend typecheck

fmt: ## Auto-format and auto-fix the backend
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

run-pipeline: ## Run the daily pipeline locally (collect, filter, score, packs, report)
	$(UV) run radar run

report: ## Print the latest daily report (local only)
	@latest=$$(ls -t data/reports/*.md 2>/dev/null | head -1); \
	if [ -z "$$latest" ]; then echo "No report yet: run 'make run-pipeline'."; exit 1; fi; \
	echo "== $$latest"; cat "$$latest"

send: ## Send APPROVED LinkedIn drafts one by one, each after your confirmation (local only)
	$(UV) run radar send

send-dry: ## Same as send, but linkedin-cli runs with --dry-run and nothing changes
	$(UV) run radar send --dry-run

build: ## Build the frontend for production (frontend/dist)
	$(PNPM) -C frontend build

schedule-install: ## Run the pipeline every day at 07:00 with launchd (runs on wake if asleep)
	./scripts/schedule.sh install

schedule-remove: ## Remove the daily launchd job
	./scripts/schedule.sh remove

schedule-status: ## Show whether the daily job is installed and the last log lines
	./scripts/schedule.sh status

schedule-run-now: ## Trigger the daily job immediately (output in logs/daily.log)
	./scripts/schedule.sh run-now

clean: ## Remove caches, build output and the demo DB (keeps data/radar.db)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov frontend/dist data/demo.db
	find . -name __pycache__ -type d -not -path './.venv/*' -not -path './frontend/node_modules/*' -prune -exec rm -rf {} +
