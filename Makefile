# Skill Trigger Graph — manual test orchestration
# Usage:  make help

SHELL := /bin/bash
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
UVICORN := .venv/bin/uvicorn
ROOT := $(shell pwd)
NEO4J_CONTAINER := skill-router-neo4j

# Auto-load .env (KEY=VALUE per line). Silent if .env is missing.
-include .env
export

.DEFAULT_GOAL := help

# ── Help ────────────────────────────────────────────────────────────

.PHONY: help
help:  ## Show this help
	@printf "\nSkill Trigger Graph — manual test targets\n\n"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z][a-zA-Z0-9_-]*:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nQuick path: \033[1mmake confidence\033[0m  (env-check + tests + Neo4j + smoke index + router + hook)\n\n"

# ── Setup ───────────────────────────────────────────────────────────

.PHONY: install
install:  ## Recreate venv via uv + install dev deps
	rm -rf .venv
	uv venv .venv
	uv pip install -e ".[dev]"

.PHONY: env-check
env-check:  ## Verify Python (uv-managed), deps, Docker, LLM creds
	@printf "\n── uv ──\n"; uv --version
	@printf "── python (uv-managed venv) ──\n"; $(PYTHON) -V
	@printf "── deps ──\n"; uv pip list --python $(PYTHON) 2>/dev/null | grep -iE "^(neo4j|fastapi|anthropic|sentence-transformers|matplotlib|networkx|claude-agent-sdk)" | head -10 || true
	@printf "── docker ──\n"; (docker --version && docker ps --filter "name=$(NEO4J_CONTAINER)" --format '  container: {{.Names}} {{.Status}}') || echo "  Docker not available"
	@printf "── llm credentials ──\n"
	@if [ -n "$$GOOGLE_API_KEY" ];   then echo "  GOOGLE_API_KEY:    set (len $${#GOOGLE_API_KEY})";  else echo "  GOOGLE_API_KEY:    unset"; fi
	@if [ -n "$$ANTHROPIC_API_KEY" ]; then echo "  ANTHROPIC_API_KEY: set (len $${#ANTHROPIC_API_KEY})"; else echo "  ANTHROPIC_API_KEY: unset"; fi
	@if [ -n "$$SKILLOGY_LLM" ];   then echo "  SKILLOGY_LLM:  $$SKILLOGY_LLM (forced)"; fi
	@$(PYTHON) -c "import claude_agent_sdk; print('  claude-agent-sdk:  importable')" 2>/dev/null || echo "  claude-agent-sdk:  not importable"
	@$(PYTHON) -c "import google.genai; print('  google-genai:      importable')" 2>/dev/null || echo "  google-genai:      not importable"
	@printf "\n"

# ── Tests ──────────────────────────────────────────────────────────

.PHONY: test
test:  ## Run unit tests (verbose)
	$(PYTEST) -v --tb=short

.PHONY: test-quiet
test-quiet:  ## Run unit tests (quiet)
	$(PYTEST) -q --tb=no

.PHONY: test-integration
test-integration: neo4j-up  ## Integration tests (Docker + Neo4j required)
	NEO4J_INTEGRATION=1 $(PYTEST) -m integration -v

# ── Neo4j ──────────────────────────────────────────────────────────

.PHONY: neo4j-up
neo4j-up:  ## Start Neo4j (Docker), wait until /browser is up (max ~2 min)
	@docker compose up -d neo4j
	@printf "Waiting for Neo4j on http://localhost:7474 "
	@for i in $$(seq 1 120); do \
	  if curl -s http://localhost:7474 -o /dev/null; then printf " ready (%ds)\n" $$i; exit 0; fi; \
	  printf "."; sleep 1; \
	done; printf "\n  Neo4j did not respond — check 'make neo4j-logs'\n"; exit 1

.PHONY: neo4j-down
neo4j-down:  ## Stop Neo4j
	docker compose down

.PHONY: neo4j-logs
neo4j-logs:  ## Tail Neo4j logs
	docker logs -f $(NEO4J_CONTAINER)

.PHONY: neo4j-browser
neo4j-browser:  ## Open Neo4j Browser (user neo4j / pass skillrouter)
	@echo "Open: http://localhost:7474  (user: neo4j, pass: skillrouter)"
	@(command -v explorer.exe >/dev/null && explorer.exe http://localhost:7474) \
	  || (command -v xdg-open >/dev/null && xdg-open http://localhost:7474) \
	  || true

# ── Indexing (Phase 1) ──────────────────────────────────────────────

.PHONY: index-smoke
index-smoke: neo4j-up  ## Index 5 SKILL.md (quick smoke, ~$0.03)
	$(PYTHON) -m skillogy index --limit 5

.PHONY: index
index: neo4j-up  ## Index ALL local SKILL.md (~600+, ~$3, takes minutes)
	$(PYTHON) -m skillogy index

TESTBED_ROOT ?= $(HOME)/skill-router-testbed/skills

.PHONY: index-testbed
index-testbed: neo4j-up  ## Index ONLY testbed skills in parallel (set TESTBED_ROOT= to override path)
	SKILLOGY_EXTRA_ROOTS=$(TESTBED_ROOT) $(PYTHON) -m skillogy index --roots $(TESTBED_ROOT) --workers 8

.PHONY: index-clear
index-clear:  ## Wipe Neo4j graph
	@$(PYTHON) -c "from skillogy.core.graph import clear_graph; clear_graph(); print('Graph cleared')"

# ── Router REPL smoke ──────────────────────────────────────────────

.PHONY: router-smoke
router-smoke:  ## Single routing query against the live graph
	@$(PYTHON) -c "\
from skillogy.core.router import Router;\
r=Router();\
res=r.find_skill('TypeScript 빌드 깨졌어');\
print('name:', res.skill_name);\
print('score:', round(res.score,3));\
print('top alts:', res.alternatives[:3]);\
print('reasoning_path:', res.reasoning_path[:5]);\
print('body[:200]:', (res.skill_body or '')[:200])"

# ── Hook smoke ─────────────────────────────────────────────────────

# Build canonical hook stdin payload via Python (avoids shell-escape pain)
define HOOK_PAYLOAD_PY
import json, os
print(json.dumps({
  "prompt": "TypeScript 빌드 깨졌어",
  "session_id": "smoke",
  "cwd": os.environ.get("ROOT", os.getcwd()),
  "hook_event_name": "UserPromptSubmit",
  "transcript_path": "",
  "permission_mode": "default",
}))
endef
export HOOK_PAYLOAD_PY

.PHONY: hook-smoke-disabled
hook-smoke-disabled:  ## Hook in DISABLED mode (no DB/LLM needed)
	@$(PYTHON) -c "$$HOOK_PAYLOAD_PY" | SKILLOGY_DISABLE=1 $(PYTHON) -m skillogy.adapters.hook

.PHONY: hook-smoke
hook-smoke:  ## Hook real-trigger smoke (Neo4j + creds required)
	@$(PYTHON) -c "$$HOOK_PAYLOAD_PY" | $(PYTHON) -m skillogy.adapters.hook

.PHONY: hook-install-snippet
hook-install-snippet:  ## Print the JSON to merge into ~/.claude/settings.json
	@printf '\nMerge this into ~/.claude/settings.json (under "hooks"):\n\n'
	@$(PYTHON) -c "import json; print(json.dumps({'hooks':{'UserPromptSubmit':[{'hooks':[{'type':'command','command':'$(ROOT)/scripts/skillogy-hook.sh'}]}]}}, indent=2))"
	@printf "\nThen restart your Claude Code session.\n\n"

# ── Web UI ─────────────────────────────────────────────────────────

.PHONY: backend
backend:  ## Run FastAPI backend on :8765 (foreground)
	$(UVICORN) skillogy.adapters.web_api:app --port 8765 --reload

.PHONY: frontend
frontend:  ## Run Vite dev server on :5173 (foreground)
	cd web && npm run dev

.PHONY: ui
ui:  ## Start backend + frontend TOGETHER (Ctrl+C stops both)
	@if [ ! -d web/node_modules ]; then echo "web/node_modules missing — running npm install…"; (cd web && npm install); fi
	@echo ""
	@echo "  Backend  → http://localhost:8765"
	@echo "  Frontend → http://localhost:5173"
	@echo "  (Ctrl+C stops both)"
	@echo ""
	@trap 'kill 0 2>/dev/null' INT TERM EXIT; \
	  ( $(UVICORN) skillogy.adapters.web_api:app --port 8765 --reload 2>&1 | sed -u 's/^/[backend ] /' ) & \
	  ( cd web && npm run dev 2>&1 | sed -u 's/^/[frontend] /' ) & \
	  wait

.PHONY: ui-bg
ui-bg:  ## Start backend + frontend in background, log to /tmp (use ui-stop to kill)
	@if [ ! -d web/node_modules ]; then echo "web/node_modules missing — running npm install…"; (cd web && npm install); fi
	@nohup $(UVICORN) skillogy.adapters.web_api:app --port 8765 --reload > /tmp/skill-router-backend.log 2>&1 & echo $$! > /tmp/skill-router-backend.pid
	@nohup bash -c 'cd web && npm run dev' > /tmp/skill-router-frontend.log 2>&1 & echo $$! > /tmp/skill-router-frontend.pid
	@sleep 2
	@echo "Backend pid=$$(cat /tmp/skill-router-backend.pid)  → http://localhost:8765   logs: /tmp/skill-router-backend.log"
	@echo "Frontend pid=$$(cat /tmp/skill-router-frontend.pid) → http://localhost:5173  logs: /tmp/skill-router-frontend.log"
	@echo "Stop both:    make ui-stop"
	@echo "Tail logs:    make ui-logs"

.PHONY: ui-stop
ui-stop:  ## Kill the background backend + frontend started by ui-bg
	@-[ -f /tmp/skill-router-backend.pid  ] && kill $$(cat /tmp/skill-router-backend.pid)  2>/dev/null && rm /tmp/skill-router-backend.pid  && echo "backend stopped"  || echo "backend not running"
	@-[ -f /tmp/skill-router-frontend.pid ] && kill $$(cat /tmp/skill-router-frontend.pid) 2>/dev/null && rm /tmp/skill-router-frontend.pid && echo "frontend stopped" || echo "frontend not running"
	@-pkill -f "uvicorn skillogy.adapters.web_api" 2>/dev/null
	@-pkill -f "vite" 2>/dev/null
	@true

.PHONY: ui-logs
ui-logs:  ## Tail backend + frontend logs (started by ui-bg)
	@tail -f /tmp/skill-router-backend.log /tmp/skill-router-frontend.log

# ── One-shot infra orchestration ───────────────────────────────────

.PHONY: start
start: neo4j-up ui-bg  ## Start ALL infra (Neo4j + backend + frontend) in background
	@printf "\n\033[32m✓ All infra running\033[0m\n"
	@printf "  Neo4j     → http://localhost:7474  (neo4j / skillrouter)\n"
	@printf "  Backend   → http://localhost:8765\n"
	@printf "  Frontend  → http://localhost:5173\n\n"
	@printf "  Logs:     \033[36mmake ui-logs\033[0m\n"
	@printf "  Stop all: \033[36mmake stop\033[0m\n\n"

.PHONY: stop
stop: ui-stop neo4j-down  ## Stop ALL infra (backend + frontend + Neo4j)
	@printf "\n\033[32m✓ All infra stopped\033[0m\n\n"

.PHONY: restart
restart: stop start  ## Stop and restart all infra

.PHONY: web-build
web-build:  ## Production build of Web UI
	cd web && npm run build

.PHONY: web-deps
web-deps:  ## Install Web UI deps
	cd web && npm install

.PHONY: api-smoke
api-smoke:  ## Smoke /api/skills + /api/graph (assumes backend already running)
	@echo "── /api/skills count ──"
	@curl -s http://127.0.0.1:8765/api/skills | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print(f'  {len(d)} skills')"
	@echo "── /api/graph keys ──"
	@curl -s http://127.0.0.1:8765/api/graph | $(PYTHON) -c "import sys,json; d=json.load(sys.stdin); print(f'  nodes={len(d.get(\"nodes\",[]))}, edges={len(d.get(\"edges\",[]))}, warning={d.get(\"warning\",\"-\")}')"

# ── Benchmark ──────────────────────────────────────────────────────

.PHONY: eval-set
eval-set:  ## Generate eval set (50 skills sampled)
	$(PYTHON) -m bench eval-set --out bench/data/eval.jsonl --n-skills 50

.PHONY: eval-set-large
eval-set-large:  ## Generate large eval set (300 skills, takes minutes + costs)
	$(PYTHON) -m bench eval-set --out bench/data/eval.jsonl --n-skills 300

.PHONY: bench
bench:  ## Run 3-condition trigger-rate benchmark
	$(PYTHON) -m bench run --eval bench/data/eval.jsonl --out-dir bench/results --conditions all

.PHONY: bench-sog-only
bench-sog-only:  ## Run only the SOG condition (fast)
	$(PYTHON) -m bench run --eval bench/data/eval.jsonl --out-dir bench/results --conditions sog

.PHONY: eval-set-testbed
eval-set-testbed:  ## Generate eval set from testbed skills only
	SKILLOGY_EXTRA_ROOTS=$(TESTBED_ROOT) $(PYTHON) -m bench eval-set \
	  --out bench/data/eval-testbed.jsonl \
	  --n-skills 29 \
	  --roots $(TESTBED_ROOT)

define _bench_claude_one
	@TS=$$(date +%Y%m%d_%H%M%S); \
	OUT=bench/results/_tmp_$(1); \
	rm -rf $$OUT; mkdir -p $$OUT; \
	BENCH_CLAUDE_MODEL=$(1) $(PYTHON) -m bench run \
	  --eval bench/data/eval-testbed.jsonl \
	  --out-dir $$OUT \
	  --conditions claude_native,claude_hook && \
	mv $$OUT/results.json bench/results/$(1)_$${TS}_results.json && \
	mv $$OUT/summary.json bench/results/$(1)_$${TS}_summary.json && \
	rmdir $$OUT && \
	echo "→ bench/results/$(1)_$${TS}_summary.json"
endef

.PHONY: bench-claude-haiku
bench-claude-haiku:  ## Bench with Claude Haiku model → {haiku}_{timestamp}_*.json
	$(call _bench_claude_one,haiku)

.PHONY: bench-claude-sonnet
bench-claude-sonnet:  ## Bench with Claude Sonnet model → {sonnet}_{timestamp}_*.json
	$(call _bench_claude_one,sonnet)

.PHONY: bench-claude-opus
bench-claude-opus:  ## Bench with Claude Opus model → {opus}_{timestamp}_*.json
	$(call _bench_claude_one,opus)

.PHONY: bench-claude-all
bench-claude-all: bench-claude-haiku bench-claude-sonnet bench-claude-opus  ## Run all three models sequentially

.PHONY: charts
charts:  ## Generate matplotlib PNG charts
	$(PYTHON) -m bench chart --summary bench/results/summary.json --out-dir bench/results
	@ls -la bench/results/*.png 2>/dev/null

.PHONY: open-hero-chart
open-hero-chart:  ## Open hero chart in system viewer
	@(command -v explorer.exe >/dev/null && explorer.exe bench/results/chart-trigger-accuracy.png) \
	  || (command -v xdg-open >/dev/null && xdg-open bench/results/chart-trigger-accuracy.png) \
	  || true

# ── Confidence: 10-min end-to-end ──────────────────────────────────

.PHONY: confidence
confidence: env-check test-quiet neo4j-up index-smoke router-smoke hook-smoke-disabled  ## End-to-end confidence run (~2 min)
	@printf "\n\033[32m✓ Confidence run complete\033[0m\n"
	@printf "Next steps:\n"
	@printf "  - Browse Neo4j data:    \033[36mmake neo4j-browser\033[0m\n"
	@printf "  - Web UI (2 terminals): \033[36mmake backend\033[0m  &  \033[36mmake frontend\033[0m  then \033[36mmake api-smoke\033[0m\n"
	@printf "  - Real hook in CC:      \033[36mmake hook-install-snippet\033[0m\n"
	@printf "  - Full benchmark:       \033[36mmake eval-set bench charts\033[0m\n\n"

# ── Cleanup ────────────────────────────────────────────────────────

.PHONY: clean-bench
clean-bench:  ## Remove benchmark outputs
	rm -f bench/results/*.png bench/results/*.json bench/data/eval.jsonl

.PHONY: clean-web
clean-web:  ## Remove Web UI build artifacts
	rm -rf web/dist web/node_modules

.PHONY: clean-venv
clean-venv:  ## Remove Python venv (use 'make install' to recreate)
	rm -rf .venv

.PHONY: clean
clean: clean-bench clean-web  ## Remove build/test artifacts (keeps venv)
	@echo "Cleaned bench + web artifacts. Use 'make clean-venv' to also drop .venv."
