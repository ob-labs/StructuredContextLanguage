.DEFAULT_GOAL := help
.PHONY: help install lint format format-check typecheck test check run build lock docker-build docker-up docker-down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (core + dev) into the uv environment
	uv sync --extra dev

lint: ## Run ruff linter
	uv run ruff check scl tests main.py

format: ## Auto-format code with ruff
	uv run ruff format scl tests main.py

format-check: ## Check formatting without modifying files
	uv run ruff format --check scl tests main.py

typecheck: ## Run mypy type checker
	uv run mypy scl

test: ## Run the test suite with coverage
	uv run pytest

check: lint format-check typecheck test ## Run all CI checks (lint, format, types, tests)

run: ## Start the SCL service
	uv run python main.py

build: ## Build the wheel and sdist into dist/
	uv build

lock: ## Refresh uv.lock
	uv lock

docker-build: ## Build the application Docker image
	docker build -t scl-app .

docker-up: ## Start app + prometheus via docker compose
	docker compose up --build

docker-down: ## Stop and remove docker compose services
	docker compose down

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -not -path '*/skills/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml *.egg-info dist build
