"""Exception-to-ErrorEvent mapping and credential redaction applied at wire egress."""

import re
from typing import TYPE_CHECKING, Final

from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import ErrorEvent
from kodezart.types.domain.credentials import CREDENTIAL_SHAPES

if TYPE_CHECKING:
    # Type-only import — keeps RaiseSite out of this module's runtime namespace
    # so consumers cannot import it from here.  RaiseSite has a single
    # authoritative home in ``kodezart.types.domain.agent`` and downstream
    # code must import it from there.
    from kodezart.types.domain.agent import RaiseSite


#: The shared credential table, compiled once with each shape's replacement
#: template beside it.  The outbound gate compiles the same patterns to find
#: spans; this surface substitutes, so it is the one that needs the
#: templates.
_COMPILED_CREDENTIAL_SHAPES: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(shape.pattern), shape.replacement) for shape in CREDENTIAL_SHAPES
)


def redact_credentials(s: str) -> str:
    """Replace vendor credential patterns with the redaction sentinel.

    Applied at the two ErrorEvent egress fields below and at both
    Claude-SDK adapter ``claude_sdk_process_error`` log calls.  Patterns
    are tightly scoped to the shapes each vendor publishes — wider matches
    risk scrubbing non-secret operator text, which the ticket explicitly
    forbids.  A vendor's taxonomy is one entry in ``CREDENTIAL_SHAPES``,
    the same table the outbound gate's shipped credential patterns come
    from, so covering the next one adds an entry rather than editing this
    function — and cannot cover one surface while leaving the other blind.

    LEAK ORIGIN vs. egress redaction: the upstream LEAK ORIGIN is
    ``adapters/subprocess_git_service.py`` — specifically ``_run``,
    ``_run_output``, and ``_run_with_exit_codes``, each of which embeds
    raw ``stderr.decode().strip()`` into a ``RuntimeError`` message via
    ``f"{cmd_repr} failed: {stderr_text}"``.  On ``git fetch`` /
    ``git push`` / ``git clone`` failure that stderr typically echoes
    the tokenized remote URL (``https://x-access-token:<token>@...``)
    constructed by ``adapters/github_token_auth.py``.  This helper
    redacts at the egress / convergence point — ``build_error_event``
    below plus the two structured-warning log sites in the Claude SDK
    adapters — rather than at the source.  Redacting at the convergence
    point means every ``Exception`` flowing into ``build_error_event``
    is scrubbed exactly once, regardless of which adapter raised it; a
    future hardening pass MAY scrub at the source as defense-in-depth
    but is not required for the wire-visible fields to be safe.
    """
    for pattern, replacement in _COMPILED_CREDENTIAL_SHAPES:
        s = pattern.sub(replacement, s)
    return s


def build_error_event(exc: Exception) -> ErrorEvent:
    """Build a typed ``ErrorEvent`` from an exception.

    Uses explicit ``isinstance`` branches against ``NoStructuredOutputError``
    and ``AgentSDKError`` — NO ``getattr(exc, ...)`` introspection
    (which returns ``Any`` and propagates into Pydantic validation,
    violating ``disallow_any_explicit``).

    Lives in ``core/`` so chains and services can reuse the same typed
    mapping without importing from ``handlers/``.
    """
    cause = exc.__cause__
    error_kind: str = type(exc).__name__
    cause_class: str | None = type(cause).__name__ if cause is not None else None
    stop_reason: str | None = None
    raise_site: RaiseSite | None = None
    rate_limit_rejected: bool | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None
    result_event_observed: bool | None = None
    subtype: str | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    result_tail: str | None = None

    if isinstance(exc, NoStructuredOutputError):
        raise_site = exc.raise_site
        stop_reason = exc.stop_reason
        rate_limit_rejected = exc.rate_limit_rejected
        result_event_observed = exc.result_event_observed
        subtype = exc.subtype
        num_turns = exc.num_turns
        duration_ms = exc.duration_ms
        result_tail = exc.result_tail
    elif isinstance(exc, AgentSDKError):
        exit_code = exc.exit_code
        stderr_tail = exc.stderr_tail

    return ErrorEvent(
        error=redact_credentials(str(exc)),
        error_kind=error_kind,
        cause_class=cause_class,
        stop_reason=stop_reason,
        raise_site=raise_site,
        rate_limit_rejected=rate_limit_rejected,
        exit_code=exit_code,
        stderr_tail=(
            redact_credentials(stderr_tail) if stderr_tail is not None else None
        ),
        result_event_observed=result_event_observed,
        subtype=subtype,
        num_turns=num_turns,
        duration_ms=duration_ms,
        result_tail=(
            redact_credentials(result_tail) if result_tail is not None else None
        ),
    )
