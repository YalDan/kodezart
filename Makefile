.DEFAULT_GOAL := check
.PHONY: install format lint lint-fix type-check test check clean \
	verify-no-origin-literal verify-no-raw-priority-sort verify-one-base-resolver

install:
	uv sync --all-groups

format:
	uv run ruff format src/ tests/
	uv run ruff check --select I --fix src/ tests/

lint:
	uv run ruff check src/ tests/

lint-fix:
	uv run ruff check --fix src/ tests/

type-check:
	uv run mypy src/

test:
	uv run pytest

verify-no-origin-literal:
	@if grep -rnE '"origin[/"]' src/kodezart --include='*.py' | grep -v 'core/config.py' ; then \
		echo 'ERROR: literal "origin" found in src/kodezart outside core/config.py' ; \
		exit 1 ; \
	fi

# The vendor's numeric priority encoding sorts NO-PRIORITY work first under
# an ascending sort and looks entirely reasonable on inspection, which is why
# this is a guard and not a review note. Two modules are allowed to know the
# encoding: the adapter that maps it and the wire shape that declares it.
verify-no-raw-priority-sort:
	@if grep -rni --include='*.py' 'priority' src/kodezart \
		| grep -v '^src/kodezart/adapters/linear_mcp_tracker.py:' \
		| grep -v '^src/kodezart/types/domain/linear_mcp.py:' \
		| awk '{ body = $$0; sub(/^[^:]*:[0-9]+:/, "", body); if (body ~ /[0-9]/) print }' \
		| grep . ; then \
		echo 'ERROR: priority paired with a digit outside the adapter and its wire shape' ; \
		exit 1 ; \
	fi

# A second base-resolution rule is how a shortcut gets added later: one call
# site takes the "obvious" path for a single blocker and the degenerate case
# stops being the general case. One definition, or the build fails.
verify-one-base-resolver:
	@found=$$(grep -rn --include='*.py' '^def resolve_base(' src/kodezart | wc -l | tr -d ' ') ; \
	if [ "$$found" != "1" ] ; then \
		echo "ERROR: expected exactly one resolve_base definition, found $$found" ; \
		grep -rn --include='*.py' '^def resolve_base(' src/kodezart ; \
		exit 1 ; \
	fi

check: verify-no-origin-literal verify-no-raw-priority-sort verify-one-base-resolver \
	lint type-check test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
