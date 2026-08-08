"""Core-layer exception classes that carry a runtime ``types/`` dependency."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from kodezart.types.domain.agent import ResultEvent

if TYPE_CHECKING:
    # Type-only import — keeps RaiseSite out of this module's runtime namespace
    # so consumers cannot import it from here.  RaiseSite has a single
    # authoritative home in ``kodezart.types.domain.agent`` and downstream
    # code must import it from there.
    from kodezart.types.domain.agent import RaiseSite


class NoStructuredOutputError(Exception):
    """Raised when an agent stream completes without producing usable output.

    Peer of ``AgentSDKError`` — deliberately NOT a subclass of
    ``TransientAPIError``/``RateLimitError``/``AgentSDKError`` so that
    ``core.retry.should_retry`` falls through to ``False``.  Soft
    failures are deterministic (agent finished but emitted no
    structured output) and not worth retrying.

    Carries primitive scalars only — no ``ResultEvent`` reference
    survives construction (mirrors ``RateLimitError``'s primitive-only
    shape and resolves the hexagonal cross-layer concern).
    """

    def __init__(
        self,
        message: str,
        *,
        raise_site: "RaiseSite",
        result_event: ResultEvent | None,
        rate_limit_rejected: bool = False,
    ) -> None:
        super().__init__(message)
        self.raise_site: RaiseSite = raise_site
        self.rate_limit_rejected: bool = rate_limit_rejected
        self.result_event_observed: bool = result_event is not None
        # Primitive snapshots — no event-object reference retained.
        self.stop_reason: str | None = (
            result_event.stop_reason if result_event is not None else None
        )
        self.is_error: bool | None = (
            result_event.is_error if result_event is not None else None
        )
        self.session_id: str | None = (
            result_event.session_id if result_event is not None else None
        )
        self.total_cost_usd: float | None = (
            result_event.total_cost_usd if result_event is not None else None
        )


class PromptResolutionError(Exception):
    """Raised at boot when prompt resolution cannot produce one template per key.

    Carries EVERY failing function key plus the sets the registry found, so a
    single boot failure names the whole gap instead of one entry at a time.
    No code path substitutes the default set for a configured override — a
    broken override is this error, never a silent downgrade.
    """

    def __init__(
        self,
        message: str,
        *,
        failing_keys: Sequence[str],
        available_sets: Sequence[str],
    ) -> None:
        detail = (
            f"{message} (failing keys: {', '.join(failing_keys) or 'none'}; "
            f"available sets: {', '.join(available_sets) or 'none'})"
        )
        super().__init__(detail)
        self.failing_keys: tuple[str, ...] = tuple(failing_keys)
        self.available_sets: tuple[str, ...] = tuple(available_sets)


class PromptRenderError(Exception):
    """Raised when a template cannot be rendered.

    ``missing`` lists every UNCONDITIONAL placeholder that had no binding,
    collected in one pass.  A placeholder referenced only inside a false
    ``{{#if}}`` block is a legal runtime state and never appears here.
    """

    def __init__(self, message: str, *, missing: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.missing: tuple[str, ...] = tuple(missing)


class SkillPreflightError(Exception):
    """Raised at boot when a configured skill name is not host-provisioned.

    Names EVERY unresolvable skill at once.  Skills are host-provided at user
    scope; a name that resolves to nothing would otherwise be forwarded
    verbatim to the SDK and silently filtered, so the check has to happen
    here — the SDK offers no session-time availability signal.
    """

    def __init__(
        self,
        message: str,
        *,
        unresolvable: Sequence[str],
        available: Sequence[str],
    ) -> None:
        detail = (
            f"{message} (unresolvable: {', '.join(unresolvable)}; "
            f"host inventory: {', '.join(available) or 'empty'})"
        )
        super().__init__(detail)
        self.unresolvable: tuple[str, ...] = tuple(unresolvable)
        self.available: tuple[str, ...] = tuple(available)


class SkillInventoryError(Exception):
    """Raised when the host's installed-plugins manifest cannot be read.

    Carries EVERY problem at once.  The manifest is the authority on which
    plugins are installed and where, so a manifest that exists but cannot be
    read as that shape has to say so: reporting an empty plugin set instead
    would reach the operator as "your skill is not provisioned" for skills
    that are provisioned.
    """

    def __init__(self, message: str, *, problems: Sequence[str]) -> None:
        detail = f"{message} ({'; '.join(problems)})"
        super().__init__(detail)
        self.problems: tuple[str, ...] = tuple(problems)


class OperationConfigError(Exception):
    """Raised when the operation config cannot be loaded or is structurally bad.

    Carries EVERY failure at once.  Structural only: live-workspace existence
    resolution belongs to the tracker adapter, not to this lane.
    """

    def __init__(self, message: str, *, failures: Sequence[str]) -> None:
        super().__init__(f"{message} ({'; '.join(failures)})")
        self.failures: tuple[str, ...] = tuple(failures)


class PromptNamespaceCollisionError(Exception):
    """Raised at boot when the three binding namespaces are not disjoint."""

    def __init__(self, message: str, *, colliding: Sequence[str]) -> None:
        super().__init__(f"{message} ({', '.join(colliding)})")
        self.colliding: tuple[str, ...] = tuple(colliding)
