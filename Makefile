# =============================================================================
# AlgoEngine - Build Automation
# =============================================================================

# ── Configuration ──────────────────────────────────────────────────────
PYTHON      := python3
PIP         := pip3
NPM         := npm
TSC         := npx tsc
CARGO       := cargo
GO          := go
DOCKER      := docker
COMPOSE     := docker compose

PROJECT     := algoengine
VERSION     := $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
BUILD_DIR   := build

# Colors for output
RED         := \033[0;31m
GREEN       := \033[0;32m
YELLOW      := \033[0;33m
BLUE        := \033[0;34m
NC          := \033[0m

.PHONY: all help setup install build test lint clean docker docs

# ── Default Target ────────────────────────────────────────────────────
all: install test build

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "$(BLUE)%-20s$(NC) %s\n", $$1, $$2}'

# ── Setup & Installation ──────────────────────────────────────────────

setup: ## Set up full development environment
	@echo "$(BLUE)[Setup] Installing all dependencies...$(NC)"
	./scripts/setup_env.sh --full

install: install-python install-dashboard ## Install all project dependencies (alias)

install-python: ## Install Python dependencies
	@echo "$(BLUE)[Python] Installing dependencies...$(NC)"
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

install-dashboard: ## Install dashboard Node.js dependencies
	@echo "$(BLUE)[Dashboard] Installing dependencies...$(NC)"
	cd dashboard && $(NPM) install

install-rust: ## Install Rust toolchain if needed
	@echo "$(BLUE)[Rust] Setting up Rust data processor...$(NC)"
	cd rust && $(CARGO) fetch

install-go: ## Download Go monitor dependencies
	@echo "$(BLUE)[Go] Downloading monitor dependencies...$(NC)"
	cd monitor && $(GO) mod download

# ── Build ─────────────────────────────────────────────────────────────

build: build-python build-dashboard build-rust build-go ## Build all components

build-python: ## Build Python package
	@echo "$(BLUE)[Python] Building package...$(NC)"
	$(PIP) install build wheel
	$(PYTHON) -m build

build-dashboard: ## Build TypeScript dashboard
	@echo "$(BLUE)[Dashboard] Compiling TypeScript...$(NC)"
	cd dashboard && $(TSC)

build-rust: ## Build Rust performance module
	@echo "$(BLUE)[Rust] Building data processor...$(NC)"
	cd rust && $(CARGO) build --release

build-go: ## Build Go monitor binary
	@echo "$(BLUE)[Go] Building monitor...$(NC)"
	cd monitor && $(GO) build -ldflags="-s -w" -o ../build/algoengine-monitor main.go

# ── Testing ───────────────────────────────────────────────────────────

test: test-python test-dashboard ## Run all tests

test-python: ## Run Python tests
	@echo "$(BLUE)[Test] Running Python tests...$(NC)"
	$(PYTHON) -m pytest tests/ -v --tb=short

test-dashboard: ## Run dashboard tests
	@echo "$(BLUE)[Test] Running dashboard tests...$(NC)"
	cd dashboard && $(NPM) test

# ── Linting ───────────────────────────────────────────────────────────

lint: lint-python lint-dashboard ## Run all linters

lint-python: ## Lint Python code
	@echo "$(BLUE)[Lint] Running Python linters...$(NC)"
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m mypy src/

lint-dashboard: ## Lint TypeScript code
	@echo "$(BLUE)[Lint] Running TypeScript linter...$(NC)"
	cd dashboard && $(NPM) run lint

# ── Docker ────────────────────────────────────────────────────────────

docker: docker-build ## Build and start Docker containers (alias)

docker-build: ## Build Docker images
	@echo "$(BLUE)[Docker] Building images...$(NC)"
	$(COMPOSE) build

docker-up: ## Start Docker services
	@echo "$(BLUE)[Docker] Starting services...$(NC)"
	$(COMPOSE) up -d

docker-down: ## Stop Docker services
	@echo "$(BLUE)[Docker] Stopping services...$(NC)"
	$(COMPOSE) down

docker-logs: ## Follow Docker logs
	$(COMPOSE) logs -f

docker-clean: ## Remove Docker containers and images
	@echo "$(YELLOW)[Docker] Cleaning up...$(NC)"
	$(COMPOSE) down -v --rmi all --remove-orphans

# ── Monitoring ─────────────────────────────────────────────────────────

monitoring-up: ## Start Prometheus + Grafana monitoring stack
	@echo "$(BLUE)[Monitoring] Starting Prometheus and Grafana...$(NC)"
	$(COMPOSE) up -d prometheus grafana
	@echo "$(GREEN)Prometheus: http://localhost:9091$(NC)"
	@echo "$(GREEN)Grafana:    http://localhost:3001 (admin/admin)$(NC)"

monitoring-down: ## Stop Prometheus + Grafana monitoring stack
	@echo "$(BLUE)[Monitoring] Stopping Prometheus and Grafana...$(NC)"
	$(COMPOSE) stop prometheus grafana

monitoring-logs: ## Follow monitoring stack logs
	$(COMPOSE) logs -f prometheus grafana

monitoring-status: ## Show monitoring stack status
	$(COMPOSE) ps prometheus grafana

# ── Development ───────────────────────────────────────────────────────

dev: ## Start development environment (engine + dashboard)
	@echo "$(BLUE)[Dev] Starting development environment...$(NC)"
	./scripts/start_live.sh --mode paper --dashboard

start: ## Start trading engine (production mode)
	@echo "$(BLUE)[Start] Starting engine...$(NC)"
	./scripts/start_live.sh --mode paper

stop: ## Stop trading engine
	@echo "$(BLUE)[Stop] Stopping engine...$(NC)"
	./scripts/stop_live.sh

# ── Cleanup ───────────────────────────────────────────────────────────

clean: ## Clean build artifacts
	@echo "$(YELLOW)[Clean] Removing build artifacts...$(NC)"
	rm -rf $(BUILD_DIR) dist *.egg-info
	rm -rf .pytest_cache __pycache__
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

clean-all: clean docker-clean ## Full cleanup (build + Docker)
	@echo "$(YELLOW)[Clean] Removing node_modules...$(NC)"
	rm -rf dashboard/node_modules
	rm -rf rust/target

# ── Distribution & Documentation ──────────────────────────────────────

dist: build ## Create distribution package
	@echo "$(BLUE)[Dist] Creating distribution...$(NC)"
	mkdir -p $(BUILD_DIR)
	tar -czf $(BUILD_DIR)/$(PROJECT)-$(VERSION).tar.gz \
		--exclude='node_modules' \
		--exclude='__pycache__' \
		--exclude='.git' \
		--exclude='*.pyc' \
		--exclude='rust/target' \
		.

docs: ## Generate documentation
	@echo "$(BLUE)[Docs] Generating documentation...$(NC)"
	cd docs && $(MAKE) html 2>/dev/null || echo "Sphinx not configured"

# ── Dashboard Components (sub-targets) ────────────────────────────────

dashboard-components: ## Generate dashboard component files
	@echo "$(BLUE)[Dashboard] Component generation not needed (source already exists)$(NC)"

# ── Git Hooks ─────────────────────────────────────────────────────────

hooks: ## Install git pre-commit hooks
	@echo "$(BLUE)[Hooks] Installing git hooks...$(NC)"
	echo '#!/bin/sh\nmake lint test' > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

# ── Version ───────────────────────────────────────────────────────────

version: ## Show project version
	@echo "$(PROJECT) version $(VERSION)"