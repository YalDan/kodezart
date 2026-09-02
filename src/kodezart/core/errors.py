"""Core-layer exception classes that carry a runtime ``types/`` dependency."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from kodezart.core.constants import RESULT_TAIL_CHARS
from kodezart.domain.errors import TransientAPIError
from kodezart.types.domain.agent import ResultEvent

if TYPE_CHECKING:
    # Type-only import — keeps RaiseSite out of this module's runtime namespace
    # so consumers cannot import it from here.  RaiseSite has a single
    # authoritative home in ``kodezart.types.domain.agent`` and downstream
    # code must import it from there.
    from kodezart.types.domain.agent import RaiseSite


def result_tail(result: str | None) -> str | None:
    """The last ``RESULT_TAIL_CHARS`` of a result payload, or ``None``.

    The tail rather than the head: the two recorded soft failures put the
    answer at the end (``"No response requested."``), and a truncated head
    would have shown the prompt echo instead.  Lives beside the failure
    snapshot that carries it, so the drain summary and the exception
    report the same bytes.
    """
    if result is None:
        return None
    return result[-RESULT_TAIL_CHARS:]


class NoStructuredOutputError(Exception):
    """Raised when an agent stream completes without producing usable output.

    Peer of ``AgentSDKError`` — deliberately NOT a subclass of
    ``TransientAPIError``/``RateLimitError``/``AgentSDKError`` so that
    ``core.retry.should_retry`` falls through to ``False``.  A
    deterministic soft failure (the agent finished and emitted no
    structured output) does not become likelier on a second attempt.

    That ground holds for an empty output and NOT for a stream the
    provider rejected on a rate limit, which is transient and clears on
    its own.  ``RateLimitedSoftFailureError`` below is the variant for
    that case, and ``soft_failure`` is the only place either is built.

    Carries primitive scalars only — no ``ResultEvent`` reference
    survives construction (mirrors ``RateLimitError``'s primitive-only
    shape and resolves the hexagonal cross-layer concern).  The variant
    fields exist because the two recorded fire deaths produced wire
    events that could not distinguish "no result event at all" from
    "a result carrying no structured output" — and the result text
    itself held the answer.
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
        self.subtype: str | None = (
            result_event.subtype if result_event is not None else None
        )
        self.num_turns: int | None = (
            result_event.num_turns if result_event is not None else None
        )
        self.duration_ms: int | None = (
            result_event.duration_ms if result_event is not None else None
        )
        self.result_tail: str | None = (
            result_tail(result_event.result) if result_event is not None else None
        )


class RateLimitedSoftFailureError(NoStructuredOutputError, TransientAPIError):
    """A soft failure whose cause was a provider rate-limit rejection.

    Both bases, and each one is load-bearing.  ``TransientAPIError``
    makes ``core.retry.should_retry`` return True with its source
    untouched, so the rejection reaches the node's existing
    ``RetryPolicy`` back-off instead of terminating the run.
    ``NoStructuredOutputError`` keeps ``build_error_event``'s existing
    branch matching, so an exhausted retry still reaches the wire
    carrying ``raiseSite`` and ``rateLimitRejected``.

    Never raised directly — ``soft_failure`` decides between this and
    its plain parent from the flag ``drain`` returns.
    """


def soft_failure(
    message: str,
    *,
    raise_site: "RaiseSite",
    result_event: ResultEvent | None,
    rate_limit_rejected: bool,
) -> NoStructuredOutputError:
    """Build the exception a drained stream with no usable output must raise.

    ONE construction site decides retryability for every soft-failure
    raise site in the codebase.  A per-site conditional would be a list,
    and the next raise site joins a list by being forgotten.
    """
    variant = (
        RateLimitedSoftFailureError if rate_limit_rejected else NoStructuredOutputError
    )
    return variant(
        message,
        raise_site=raise_site,
        result_event=result_event,
        rate_limit_rejected=rate_limit_rejected,
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


class TrackerBootValidationError(Exception):
    """Raised at boot when a configured tracker mapping does not resolve.

    Lists EVERY unresolvable entry at once, each described by kind, semantic
    name and configured identifier.  There is no partial operation: a
    mapping the workspace cannot resolve would otherwise surface as a
    mis-targeted write hours later, so the process refuses to start.
    """

    def __init__(self, message: str, *, unresolved: Sequence[str]) -> None:
        super().__init__(f"{message} ({'; '.join(unresolved)})")
        self.unresolved: tuple[str, ...] = tuple(unresolved)


class PassGateScopeError(Exception):
    """Raised at construction when a container cannot serve a gate's signal.

    The other half of the container partition, and not the same failure as
    an absent one: this container is DECLARED and cannot answer the
    question anyway.  Refused where the containers meet the signal rather
    than on the tick that reaches one, because a gate built over such a
    container raises on every tick forever and the pass it guards never
    runs again.
    """

    def __init__(self, message: str, *, signal: str, container: str) -> None:
        super().__init__(f"{message} (signal: {signal}; container: {container})")
        self.signal: str = signal
        self.container: str = container


class PassGateCapabilityError(Exception):
    """Raised at boot when the credential cannot answer a wired gate's signal.

    Lists EVERY refused signal at once, each carrying the pass it gates and
    the backend's own diagnosis, so one boot failure names the whole gap.
    The alternative is what this replaces: a gate whose scan the credential
    is not scoped for reports "nothing moved" on every tick, which is
    indistinguishable from a quiet board — the pass it guards never runs
    again and nothing anywhere says so.
    """

    def __init__(self, message: str, *, refusals: Sequence[str]) -> None:
        super().__init__(f"{message} ({'; '.join(refusals)})")
        self.refusals: tuple[str, ...] = tuple(refusals)


class PassKnowledgeCapabilityError(Exception):
    """Raised at boot when a pass is told to reach a store it holds no grant to.

    Lists EVERY affected entry at once, so one boot failure names the whole
    gap rather than one registry entry per boot cycle.  The two halves of
    the mismatch are declared in different files and neither one is wrong
    on its own: an operation may name a knowledge destination and a
    deployment may grant the knowledge server to no session type.  Together
    they instruct a scheduled pass to write where its session cannot
    reach — an instruction that can only fail inside the session, on a
    board nobody is watching, with the pass reporting an ordinary run.
    """

    def __init__(self, message: str, *, destinations: Sequence[str]) -> None:
        super().__init__(f"{message} ({'; '.join(destinations)})")
        self.destinations: tuple[str, ...] = tuple(destinations)


class TrackerEnsureConflictError(Exception):
    """Raised when instating an OWNED value would ALTER an existing definition.

    Ensuring is creates-only.  A rename, a recolour, a re-scope or two
    declared members claiming one backend value is this error and performs
    no write: adopting the wrong definition silently would repurpose a
    value another part of the workspace already means something by.
    """

    def __init__(self, message: str, *, entry: str) -> None:
        super().__init__(f"{message} ({entry})")
        self.entry: str = entry


class TrackerProtocolError(Exception):
    """Raised when a tracker backend's response cannot be read as its shape.

    The adapter refuses to guess.  A field that is absent, of the wrong
    type, or carries an unmappable value is this error and never a
    substituted default — a silently defaulted priority or state would
    reorder the dispatch queue with nothing to falsify.
    """

    def __init__(self, message: str, *, tool: str, detail: str) -> None:
        super().__init__(f"{message} (tool: {tool}; {detail})")
        self.tool: str = tool
        self.detail: str = detail


class McpTransportError(Exception):
    """Raised when an MCP session cannot be opened or a tool call cannot answer.

    The transport refuses to guess in either direction: a server that
    reports a tool error, returns no structured content, or is not dialled
    at all is this error, never an empty result a caller would read as "no
    such issue".
    """

    def __init__(
        self,
        message: str,
        *,
        server_name: str,
        tool_name: str | None = None,
    ) -> None:
        described = server_name if tool_name is None else f"{server_name}/{tool_name}"
        super().__init__(f"{message} ({described})")
        self.server_name: str = server_name
        self.tool_name: str | None = tool_name


class McpSessionClosedError(McpTransportError):
    """Raised when the SESSION could not carry the call, refusal or not.

    The discriminator the record path reads (KOD-177): a server that
    ANSWERED — with a result or with a tool error — is a server that is
    there, and the remedy is the payload or the destination; a session
    that is gone is a transport to reopen or a process to diagnose, and
    the measured boot spent nine minutes writing nothing because the two
    reached the log as one indistinguishable event.

    A subclass rather than a peer, because every existing handler of a
    transport failure treats this as one: what is new is only that the
    class SAYS which of the two it was.
    """


class McpCredentialRefusedError(Exception):
    """Raised when an MCP server refuses the CREDENTIAL rather than the call.

    Deliberately not an ``McpTransportError`` and deliberately outside every
    class the tracker adapter retries.  A transport failure is a blip that a
    second attempt may clear; a refused credential answers every attempt the
    same way, so retrying one spends a whole budget of sleeps to learn what
    the first answer already said.

    Measured 2026-09-01 (KOD-171): fifty-one minutes into a live boot the
    tracker began answering HTTP 401, and claim renewals, gate scans and
    dispatch ticks each burned their full retry budget on it.
    """

    def __init__(
        self,
        message: str,
        *,
        server_name: str,
        tool_name: str | None = None,
    ) -> None:
        described = server_name if tool_name is None else f"{server_name}/{tool_name}"
        super().__init__(f"{message} ({described})")
        self.server_name: str = server_name
        self.tool_name: str | None = tool_name


class TrackerCredentialShapeError(Exception):
    """Raised at boot when the tracker credential is not the accepted shape.

    Names the FIELD it was read from and the SHAPE that backend accepts,
    because between them those are the whole of what an operator can act
    on: mint this, put it there.  A refusal that named only what was wrong
    would leave the next paste to guesswork.

    The backend takes one credential that outlives a run and one that does
    not, and nothing in this process refreshes anything, so a boot that
    accepted the second would serve until the token died and then answer
    every tracker call with a refusal, hours later, on a board nobody is
    watching — measured 2026-09-01 (KOD-171).
    """

    def __init__(self, message: str, *, field: str, accepted_shape: str) -> None:
        super().__init__(f"{message} ({field} must hold {accepted_shape})")
        self.field: str = field
        self.accepted_shape: str = accepted_shape


class PromptNamespaceCollisionError(Exception):
    """Raised at boot when the three binding namespaces are not disjoint."""

    def __init__(self, message: str, *, colliding: Sequence[str]) -> None:
        super().__init__(f"{message} ({', '.join(colliding)})")
        self.colliding: tuple[str, ...] = tuple(colliding)


class TicketReviewModeError(Exception):
    """Raised at construction when the ticket loop's mode cannot be honoured.

    Two shapes, one class, because they are one defect: a knob is
    configured whose guarantee this composition cannot deliver.  An
    explicit review budget under a mode that compiles no review arm would
    be silently ignored, and a create-only mode over a prompt set that
    declares no draft critic would run the single creator session with
    nothing checking it at all.  Both name EVERY setting involved — a
    message naming only the one that raised leaves an operator to guess
    which pair disagreed.
    """

    def __init__(self, message: str, *, settings: Sequence[str]) -> None:
        super().__init__(f"{message} ({', '.join(settings)})")
        self.settings: tuple[str, ...] = tuple(settings)


class ContentScannerBootError(Exception):
    """Raised at boot when the judgment scanner is enabled with nothing to judge.

    The third state of the enable knob, made loud.  Enabled with no
    private-surface description would leave a registered scanner whose every
    scan is ``NOT_CONFIGURED`` — a gate that blocks every authored write, or
    worse, one an operator disables to get work done.  The process refuses
    to start instead.
    """

    def __init__(self, message: str, *, missing: str) -> None:
        super().__init__(f"{message} ({missing})")
        self.missing: str = missing


class RunRecordWriteError(Exception):
    """Raised when a run's declared destination did not take its record.

    The one class the two record producers catch, and the reason they can
    log ONE field set: which kind ran, which destination it was owed to,
    whose system holds it, and which of the three failure classes it was.
    The measured boot logged a bare error string per failed write, so a
    dead knowledge session and a refused page read identically and neither
    named the log that went unwritten (KOD-177).

    The fields are plain strings — the enum VALUES their producers carry —
    because this module is under the domain vocabulary rather than over
    it, and an event's fields are strings by the time they are read.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        destination: str,
        system: str,
        failure: str,
    ) -> None:
        super().__init__(f"{message} ({kind} → {system}/{destination}: {failure})")
        self.kind: str = kind
        self.destination: str = destination
        self.system: str = system
        self.failure: str = failure

    @property
    def cause_type(self) -> str:
        """The class that actually failed, for the producers' one event.

        This class itself when nothing else raised — a wiring defect is
        raised here rather than caught from anywhere — and the underlying
        class in every other case, because "RunRecordWriteError" alone
        tells an operator only that a record failed to land.
        """
        cause = self.__cause__
        return type(self if cause is None else cause).__name__
