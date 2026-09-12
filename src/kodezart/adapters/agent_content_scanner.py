"""The judgment half of the outbound gate — a scanner backed by a session.

The same fresh session judges mandatory authored tracker aggregates and optional
organization-privacy disclosures. Credentials remain local and run first. Durable
authored text includes artifact leaves; a zero-reference claim can still describe
the tracker's changing state, and ordinary repository counts are not such claims.

The session is deliberately a DIFFERENT one from the writer whose output it
grades: no shared context, ``allowed_tools=[]``, and a neutral working
directory rather than the cloned target repository.  A model checking its
own output is not a check, and an auditor whose working directory is
attacker-writable is not one either.

Every way of having no answer resolves to a typed ``ScanFailureKind`` and
therefore to ``BLOCKED``.  Nothing here returns "no hits" for a scan that
did not happen.
"""

import asyncio

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ResultError
from pydantic import ValidationError

from kodezart.adapters._sdk_mapping import result_failure
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import PromptRenderError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, PromptSetProvider
from kodezart.core.stream_drain import drain
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import CONTENT_AUDIT_SCHEMA, ContentAuditOutput
from kodezart.types.domain.gating import (
    TRACKER_ROSTER_MIN_REFERENCES,
    DurabilityCategory,
    OutboundDestination,
    OutboundSurface,
    ScanFailureKind,
    ScanHit,
    ScanResult,
    SurfaceDurability,
    durability_of,
    surface_of,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import (
    PermissionMode,
    SessionFailureKind,
    SessionType,
)
from kodezart.types.domain.skills import SkillsSelection

_AUDIT_PERMISSION_MODE = PermissionMode.INTERACTIVE

#: Failure kinds a retry can plausibly change. Anything else is a settled
#: answer of "no answer" and retrying it only spends money.
_RETRYABLE: frozenset[ScanFailureKind] = frozenset(
    {
        ScanFailureKind.TIMEOUT,
        ScanFailureKind.RATE_LIMITED,
        ScanFailureKind.TRANSPORT_ERROR,
    },
)


_SCAN_FAILURES = {
    SessionFailureKind.TIMEOUT: ScanFailureKind.TIMEOUT,
    SessionFailureKind.REFUSAL: ScanFailureKind.REFUSAL,
    SessionFailureKind.RATE_LIMITED: ScanFailureKind.RATE_LIMITED,
    SessionFailureKind.TRANSPORT_ERROR: ScanFailureKind.TRANSPORT_ERROR,
    SessionFailureKind.BUDGET_EXHAUSTED: ScanFailureKind.BUDGET_EXHAUSTED,
    SessionFailureKind.MALFORMED_OUTPUT: ScanFailureKind.MALFORMED_VERDICT,
    SessionFailureKind.EXECUTION_ERROR: ScanFailureKind.EXECUTION_ERROR,
}


class AgentContentScanner:
    """``ContentJudgment`` that dispatches an adversarial audit session."""

    def __init__(
        self,
        *,
        executor: AgentExecutor,
        prompts: PromptSetProvider,
        neutral_cwd: str,
        skills: SkillsSelection,
        retry: RetryPolicy,
        timeout_seconds: float,
        inspect_privacy: bool = True,
    ) -> None:
        self._executor = executor
        self._prompts = prompts
        self._neutral_cwd = neutral_cwd
        self._skills = skills
        self._retry = retry
        self._timeout_seconds = timeout_seconds
        self._inspect_privacy = inspect_privacy
        self._log: BoundLogger = get_logger(__name__)

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        """Audit *content* for *destination*, or say why there is no answer."""
        aggregates = durability_of(destination) is SurfaceDurability.DURABLE
        privacy = (
            self._inspect_privacy
            and surface_of(destination) is not OutboundSurface.REPOSITORY
        )
        if not aggregates and not privacy:
            return ScanResult()
        try:
            prompt = self._prompts.template_for(PromptKey.CONTENT_AUDIT).render(
                {
                    "content": content,
                    "destination": destination.value,
                    "inspect_aggregates": True if aggregates else None,
                    "inspect_privacy": True if privacy else None,
                    "roster_minimum": TRACKER_ROSTER_MIN_REFERENCES,
                },
            )
        except PromptRenderError:
            # The mandate has no private-surface description to judge
            # against. A scanner registered without its configuration is a
            # blocked payload, never a quietly absent scanner.
            return ScanResult(failure=ScanFailureKind.NOT_CONFIGURED)
        result = ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        for attempt in range(self._retry.attempts):
            result = await self._attempt(prompt=prompt, content=content)
            if result.failure is None:
                if not privacy and any(
                    not isinstance(hit.category, DurabilityCategory)
                    for hit in result.hits
                ):
                    return ScanResult(failure=ScanFailureKind.MALFORMED_VERDICT)
                return result.model_copy(
                    update={
                        "hits": tuple(
                            hit
                            for hit in result.hits
                            if (
                                aggregates
                                if isinstance(hit.category, DurabilityCategory)
                                else privacy
                            )
                        )
                    }
                )
            if result.failure not in _RETRYABLE:
                return result
            await self._log.awarning(
                "content_audit_attempt_failed",
                attempt=attempt + 1,
                failure=result.failure.value,
                destination=destination.value,
            )
            if attempt + 1 < self._retry.attempts:
                await asyncio.sleep(self._retry.delay(attempt))
        return result

    async def _attempt(self, *, prompt: str, content: str) -> ScanResult:
        """One audit session, mapped onto hits or a typed failure kind."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result_event, rate_limit_rejected = await drain(
                    self._executor.stream(
                        prompt=prompt,
                        cwd=self._neutral_cwd,
                        permission_mode=_AUDIT_PERMISSION_MODE,
                        allowed_tools=[],
                        skills=self._prompts.session_skills(
                            PromptKey.CONTENT_AUDIT,
                            self._skills,
                        ),
                        session_type=SessionType.CONTENT_AUDIT,
                        session_policy=self._prompts.session_policy(
                            PromptKey.CONTENT_AUDIT,
                        ),
                        output_format={
                            "type": "json_schema",
                            "schema": CONTENT_AUDIT_SCHEMA,
                        },
                    ),
                    site="content_audit",
                )
        except TimeoutError:
            return ScanResult(failure=ScanFailureKind.TIMEOUT)
        except OSError:
            return ScanResult(failure=ScanFailureKind.TRANSPORT_ERROR)
        except AgentSDKError as exc:
            return ScanResult(failure=_failure_for_sdk(exc))

        if rate_limit_rejected:
            return ScanResult(failure=ScanFailureKind.RATE_LIMITED)
        if result_event is None:
            return ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        if result_event.failure_kind is not None:
            return ScanResult(failure=_SCAN_FAILURES[result_event.failure_kind])
        if result_event.is_error:
            return ScanResult(failure=ScanFailureKind.EXECUTION_ERROR)
        if result_event.structured_output is None:
            return ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        try:
            audit = ContentAuditOutput.model_validate(result_event.structured_output)
        except ValidationError:
            return ScanResult(failure=ScanFailureKind.MALFORMED_VERDICT)
        return _hits_from(audit, content=content)


def _failure_for_sdk(error: AgentSDKError) -> ScanFailureKind:
    """Read native causes here, without widening domain errors or SSE frames."""
    cause = error.__cause__
    if isinstance(cause, ResultError):
        failure = result_failure(cause)
        return (
            _SCAN_FAILURES[failure]
            if failure is not None
            else ScanFailureKind.EXECUTION_ERROR
        )
    if isinstance(cause, CLINotFoundError):
        return ScanFailureKind.NOT_CONFIGURED
    if isinstance(cause, CLIConnectionError):
        return ScanFailureKind.TRANSPORT_ERROR
    return ScanFailureKind.EXECUTION_ERROR


def _hits_from(audit: ContentAuditOutput, *, content: str) -> ScanResult:
    """Convert the audit verdict into hits, rejecting unresolvable spans.

    A span the session reports that does not lie inside the payload cannot
    be excised, and guessing at what was meant would be a fallback: the
    whole result becomes ``SPANS_UNRESOLVABLE`` and the payload blocks.
    """
    hits: list[ScanHit] = []
    for finding in audit.findings:
        if finding.start is None and finding.end is None:
            hits.append(
                ScanHit(
                    category=finding.category,
                    rationale=finding.rationale,
                ),
            )
            continue
        if not _span_lies_inside(finding.start, finding.end, content):
            return ScanResult(failure=ScanFailureKind.SPANS_UNRESOLVABLE)
        hits.append(
            ScanHit(
                category=finding.category,
                start=finding.start,
                end=finding.end,
                rationale=finding.rationale,
                matched_text=(
                    content[finding.start : finding.end]
                    if isinstance(finding.category, DurabilityCategory)
                    else None
                ),
            ),
        )
    hits.sort(key=lambda hit: hit.sort_key())
    return ScanResult(hits=tuple(hits))


def _span_lies_inside(start: int | None, end: int | None, content: str) -> bool:
    """Whether ``[start, end)`` is a real, non-empty span of *content*."""
    if start is None or end is None:
        return False
    return 0 <= start < end <= len(content)
