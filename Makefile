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
#
# The defect has one shape and it is an ORDERING one: priority reaching a
# sort key, a comparison or a selection. A digit-only predicate passed the
# natural form (`sorted(issues, key=lambda i: i.raw_priority)`) because that
# line carries no digit at all, so ordering tokens are the predicate and the
# digit is one of them. `priority_rank` — the domain order, and the only
# legitimate way priority reaches a comparison — is the single exemption,
# which is why RankKey's field carries that name rather than `priority`.
verify-no-raw-priority-sort:
	@if grep -rni --include='*.py' 'priority' src/kodezart \
		| grep -v '^src/kodezart/adapters/linear_mcp_tracker.py:' \
		| grep -v '^src/kodezart/types/domain/linear_mcp.py:' \
		| grep -vi 'priority_rank' \
		| sed 's/->//g' \
		| awk '{ body = $$0; sub(/^[^:]*:[0-9]+:/, "", body); \
			if (tolower(body) ~ /[0-9]|sort|key[[:space:]]*=|min\(|max\(|<|>|cmp/) print }' \
		| grep . ; then \
		echo 'ERROR: priority reaches an ordering outside the adapter and its wire shape' ; \
		exit 1 ; \
	fi

# A second base-resolution rule is how a shortcut gets added later: one call
# site takes the "obvious" path for a single blocker and the degenerate case
# stops being the general case. One definition, or the build fails.
#
# Anchored at column 0 the guard saw only module-level functions, so the
# most likely second definition — a class method, indented — was exactly the
# shape it could not see. The pattern matches any definition of the symbol,
# at any indentation, sync or async.
RESOLVE_BASE_DEF := (^|[[:space:]])(async[[:space:]]+)?def[[:space:]]+resolve_base[[:space:]]*\(

verify-one-base-resolver:
	@found=$$(grep -rnE --include='*.py' '$(RESOLVE_BASE_DEF)' src/kodezart | wc -l | tr -d ' ') ; \
	if [ "$$found" != "1" ] ; then \
		echo "ERROR: expected exactly one resolve_base definition, found $$found" ; \
		grep -rnE --include='*.py' '$(RESOLVE_BASE_DEF)' src/kodezart ; \
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
