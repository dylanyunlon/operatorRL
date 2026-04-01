###############################################################################
# OperatorRL lolbot-HyperAI · Makefile
# 自部署 自环境反馈 自演化
#
# Unified entry point for build, test, run, deploy operations.
# Designed for both development (bare metal) and production (Docker).
#
# Usage:
#   make help          — Show available targets
#   make install       — Install Python dependencies
#   make test          — Run full test suite
#   make run           — Start lolbot with structured logging
#   make docker-build  — Build Docker image
#   make docker-up     — Start with Docker Compose
#   make lint          — Run linting and type checks
#   make clean         — Remove caches and temp files
###############################################################################

.PHONY: help install test run lint clean docker-build docker-up docker-down \
        check-deps validate smoke-test run-debug run-dashboard

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
PYTHON      ?= python3
PIP         ?= pip3
PYTEST      ?= $(PYTHON) -m pytest
PROJECT_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
DOCKER_TAG  ?= lolbot-hyperai:latest

# Environment defaults
LOLBOT_BASE_TICK_MS ?= 10
LOLBOT_LOG_LEVEL    ?= INFO
LOLBOT_TTS_BACKEND  ?= disabled

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
help: ## Show this help
	@echo "lolbot-HyperAI · OperatorRL"
	@echo "============================"
	@echo ""
	@echo "Development:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Environment Variables:"
	@echo "  LOLBOT_BASE_TICK_MS   Tick interval (default: $(LOLBOT_BASE_TICK_MS))"
	@echo "  LOLBOT_LOG_LEVEL      Log level (default: $(LOLBOT_LOG_LEVEL))"
	@echo "  LOLBOT_TTS_BACKEND    TTS backend (default: $(LOLBOT_TTS_BACKEND))"

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------
install: ## Install Python dependencies
	$(PIP) install --break-system-packages -q \
		pytest pytest-asyncio aiohttp edge-tts pyttsx3 \
		2>/dev/null || \
	$(PIP) install -q \
		pytest pytest-asyncio aiohttp edge-tts pyttsx3

check-deps: ## Check that all core modules are importable
	@echo "Checking module imports..."
	@cd $(PROJECT_DIR) && $(PYTHON) -c " \
		import sys; sys.path.insert(0, '.'); \
		mods = [ \
			'runtime.process_manager', \
			'runtime.health_monitor', \
			'runtime.metrics_collector', \
			'runtime.error_recovery', \
			'runtime.graceful_shutdown', \
			'integration.event_dispatcher', \
			'integration.module_registry', \
			'integration.pipeline_builder', \
			'integration.agent_os_bridge', \
			'integration.plugin_loader', \
		]; \
		ok = 0; fail = 0; \
		[(__import__(m), print(f'  ✓ {m}'), ok:=ok+1) if not None else None for m in mods] \
		if False else None; \
		results = []; \
		[results.append(m) for m in mods]; \
		errors = []; \
		exec('for m in mods:\n try:\n  __import__(m)\n  print(f\"  ✓ {m}\")\n except ImportError as e:\n  print(f\"  ✗ {m}: {e}\")\n  errors.append(m)'); \
		print(f'\n{len(mods)-len(errors)}/{len(mods)} modules OK'); \
		sys.exit(1 if errors else 0); \
	"

validate: check-deps ## Validate project structure and imports
	@echo ""
	@echo "Validating project structure..."
	@for dir in runtime integration tests deploy scripts; do \
		if [ -d "$(PROJECT_DIR)/$$dir" ]; then \
			echo "  ✓ $$dir/"; \
		else \
			echo "  ✗ $$dir/ MISSING"; \
		fi; \
	done
	@echo ""
	@echo "File count:"
	@find $(PROJECT_DIR) -name "*.py" | wc -l | xargs echo "  Python files:"
	@find $(PROJECT_DIR) -name "*.py" -exec cat {} \; | wc -l | xargs echo "  Total lines:"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test: ## Run full test suite
	cd $(PROJECT_DIR) && $(PYTEST) tests/ -v --tb=short -x

test-quick: ## Run tests without slow markers
	cd $(PROJECT_DIR) && $(PYTEST) tests/ -v --tb=short -x -m "not slow"

test-coverage: ## Run tests with coverage report
	cd $(PROJECT_DIR) && $(PYTEST) tests/ -v --tb=short \
		--cov=runtime --cov=integration --cov-report=term-missing

test-loop: ## Test only the main loop and scheduler
	cd $(PROJECT_DIR) && $(PYTEST) tests/test_main_loop.py -v --tb=short

test-state: ## Test state machine and error recovery
	cd $(PROJECT_DIR) && $(PYTEST) tests/test_state_machine.py -v --tb=short

test-pipeline: ## Test pipeline and event dispatcher
	cd $(PROJECT_DIR) && $(PYTEST) tests/test_pipeline.py -v --tb=short

test-perception: ## Test perception interface compatibility
	cd $(PROJECT_DIR) && $(PYTEST) tests/test_perception.py -v --tb=short

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
run: ## Start lolbot-HyperAI with structured logging
	cd $(PROJECT_DIR) && $(PYTHON) scripts/run_with_logs.py \
		--tick-ms $(LOLBOT_BASE_TICK_MS) \
		--log-level $(LOLBOT_LOG_LEVEL)

run-debug: ## Start with debug logging and human-readable output
	cd $(PROJECT_DIR) && $(PYTHON) scripts/run_with_logs.py \
		--tick-ms 100 \
		--log-level DEBUG \
		--no-json-logs

run-dashboard: ## Start dashboard-only mode (no game capture)
	cd $(PROJECT_DIR) && $(PYTHON) -c " \
		import http.server, json; \
		class H(http.server.BaseHTTPRequestHandler): \
			def do_GET(self): \
				self.send_response(200); \
				self.send_header('Content-Type','application/json'); \
				self.end_headers(); \
				self.wfile.write(json.dumps({'overall':'healthy','mode':'dashboard'}).encode()); \
			def log_message(self,*a): pass; \
		s=http.server.HTTPServer(('0.0.0.0',8080),H); \
		print('Dashboard: http://localhost:8080/health'); \
		s.serve_forever()" \

smoke-test: ## Quick smoke test — start, run 2 seconds, stop
	@echo "Smoke test: start → run 2s → stop..."
	@cd $(PROJECT_DIR) && timeout 5 $(PYTHON) -c " \
		import asyncio, sys; sys.path.insert(0,'.'); \
		from runtime.process_manager import ProcessManager, _DummyComponent; \
		async def test(): \
			pm = ProcessManager(base_tick_ms=100, enable_signal_handling=False); \
			pm.register(_DummyComponent('smoke'), interval_ms=100, priority=0); \
			async def stop(): \
				await asyncio.sleep(2); await pm.request_shutdown(); \
			asyncio.create_task(stop()); \
			await pm.start(); \
			s = pm.get_status(); \
			print(f'Ticks: {s[\"total_ticks\"]}, Overruns: {s[\"total_overruns\"]}'); \
			assert s['total_ticks'] > 10, 'Too few ticks'; \
			print('SMOKE TEST PASSED'); \
		asyncio.run(test()); \
	" && echo "✓ Smoke test passed" || echo "✗ Smoke test failed"

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
docker-build: ## Build Docker image
	cd $(PROJECT_DIR) && docker build -t $(DOCKER_TAG) -f deploy/Dockerfile ..

docker-up: ## Start with Docker Compose
	cd $(PROJECT_DIR)/deploy && docker-compose up -d

docker-down: ## Stop Docker Compose
	cd $(PROJECT_DIR)/deploy && docker-compose down

docker-logs: ## Follow Docker logs
	cd $(PROJECT_DIR)/deploy && docker-compose logs -f lolbot

docker-shell: ## Shell into running container
	docker exec -it lolbot-hyperai /bin/bash

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------
lint: ## Run linting checks
	@echo "Running syntax checks..."
	@cd $(PROJECT_DIR) && find . -name "*.py" -not -path "./.git/*" | \
		xargs -I{} $(PYTHON) -m py_compile {} && \
		echo "✓ All files compile successfully" || \
		echo "✗ Compilation errors found"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: ## Remove caches and temp files
	find $(PROJECT_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find $(PROJECT_DIR) -type f -name "*.pyc" -delete 2>/dev/null || true
	find $(PROJECT_DIR) -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find $(PROJECT_DIR) -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(PROJECT_DIR)/data/logs/*.jsonl 2>/dev/null || true
	@echo "Cleaned."
