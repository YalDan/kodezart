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

# tests/ is deliberately outside the type gate — the errors there are
# structural (invariance classes), a workstream and not gate hygiene. The
# tests tree is exercised by execution on every gate run; this is the
# recorded decision KOD-140 requires for anything the gate excludes.
# Ruled 2026-08-31, re-stated 2026-09-04. The SIZE of the excluded set is
# deliberately not written here: a transcribed count is a number that goes
# stale on the next commit and reads as a measurement long after it stops
# being one (it said 149 in 23 files while the truth was near triple that).
# `uv run mypy src tests` names the current set whenever anybody wants it.
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
