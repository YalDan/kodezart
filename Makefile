.DEFAULT_GOAL := check
.PHONY: install format format-check lint lint-fix type-check test check clean \
	verify-no-origin-literal

install:
	uv sync --all-groups

format:
	uv run ruff format src/ tests/
	uv run ruff check --select I --fix src/ tests/

format-check:
	uv run ruff format --check src/ tests/

lint:
	uv run ruff check src/ tests/

lint-fix:
	uv run ruff check --fix src/ tests/

# tests/ is deliberately outside the type gate — measured 2026-08-31:
# 149 strict-mode errors in 23 files, structural (invariance classes),
# a workstream and not gate hygiene. The tests tree is exercised by
# execution on every gate run; this line is the recorded decision
# KOD-140 requires for anything the gate excludes.
type-check:
	uv run mypy src/

test:
	uv run pytest

verify-no-origin-literal:
	@if grep -rnE '"origin[/"]' src/kodezart --include='*.py' | grep -v 'core/config.py' ; then \
		echo 'ERROR: literal "origin" found in src/kodezart outside core/config.py' ; \
		exit 1 ; \
	fi

check: verify-no-origin-literal format-check lint type-check test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
