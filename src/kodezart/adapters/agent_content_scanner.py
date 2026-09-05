"""The judgment half of the outbound gate — a scanner backed by a session.

One implementation of ``ContentScanner`` alongside ``RegexContentScanner``,
registered AFTER it in the gate's ordered list.  A credential is arithmetic
and stays with the patterns; "would a stranger learn something from this
that this organisation did not choose to publish" is irreducibly semantic,
and no pattern set can answer it — the set of private things is open-ended,
writing the deny pattern publishes the string it protects, and the same
string can be fine or not depending on where it is going.

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

from pydantic import ValidationError

from kodezart.core.errors import PromptRenderError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, PromptSetProvider
from kodezart.core.stream_drain import drain
from kodezart.types.domain.agent import CONTENT_AUDIT_SCHEMA, ContentAuditOutput
from kodezart.types.domain.gating import (
    JUDGMENT_ROUTING,
    OutboundDestination,
    RedactionCategory,
    ScanFailureKind,
    ScanHit,
    ScannerRouting,
    ScanResult,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection

_AUDIT_PERMISSION_MODE = "default"

#: Failure kinds a retry can plausibly change. Anything else is a settled
#: answer of "no answer" and retrying it only spends money.
_RETRYABLE: frozenset[ScanFailureKind] = frozenset(
    {
        ScanFailureKind.TIMEOUT,
        ScanFailureKind.RATE_LIMITED,
        ScanFailureKind.TRANSPORT_ERROR,
    },
)


class AgentContentScanner:
    """``ContentScanner`` that dispatches an adversarial audit session."""

    def __init__(
        self,
        *,
        executor: AgentExecutor,
        prompts: PromptSetProvider,
        neutral_cwd: str,
        skills: SkillsSelection,
        retry_max_attempts: int,
        retry_initial_interval: float,
        timeout_seconds: float,
    ) -> None:
        self._executor = executor
        self._prompts = prompts
        self._neutral_cwd = neutral_cwd
        self._skills = skills
        self._retry_max_attempts = retry_max_attempts
        self._retry_initial_interval = retry_initial_interval
        self._timeout_seconds = timeout_seconds
        self._log: BoundLogger = get_logger(__name__)

    @property
    def routing(self) -> ScannerRouting:
        """Authored prose on a publication or tracker surface, plus the ref."""
        return JUDGMENT_ROUTING

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        """Audit *content* for *destination*, or say why there is no answer."""
        try:
            prompt = self._prompts.template_for(PromptKey.CONTENT_AUDIT).render(
                {"content": content, "destination": destination.value},
            )
        except PromptRenderError:
            # The mandate has no private-surface description to judge
            # against. A scanner registered without its configuration is a
            # blocked payload, never a quietly absent scanner.
            return ScanResult(failure=ScanFailureKind.NOT_CONFIGURED)
        interval = self._retry_initial_interval
        result = ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        for attempt in range(1, self._retry_max_attempts + 1):
            result = await self._attempt(prompt=prompt, content=content)
            if result.failure is None or result.failure not in _RETRYABLE:
                return result
            await self._log.awarning(
                "content_audit_attempt_failed",
                attempt=attempt,
                failure=result.failure.value,
                destination=destination.value,
            )
            if attempt < self._retry_max_attempts:
                await asyncio.sleep(interval)
                interval *= 2
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

        if rate_limit_rejected:
            return ScanResult(failure=ScanFailureKind.RATE_LIMITED)
        if result_event is None:
            return ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        if result_event.is_error:
            return ScanResult(failure=_failure_for_subtype(result_event.subtype))
        if result_event.structured_output is None:
            return ScanResult(failure=ScanFailureKind.EMPTY_RESPONSE)
        try:
            audit = ContentAuditOutput.model_validate(result_event.structured_output)
        except ValidationError:
            return ScanResult(failure=ScanFailureKind.MALFORMED_VERDICT)
        return _hits_from(audit, content=content)


def _failure_for_subtype(subtype: str) -> ScanFailureKind:
    """Map an errored result's subtype onto the taxonomy. Never CLEAN."""
    normalised = subtype.lower()
    if "budget" in normalised or "cost" in normalised:
        return ScanFailureKind.BUDGET_EXHAUSTED
    if "rate" in normalised or "limit" in normalised:
        return ScanFailureKind.RATE_LIMITED
    if "refus" in normalised or "block" in normalised:
        return ScanFailureKind.REFUSAL
    if "timeout" in normalised:
        return ScanFailureKind.TIMEOUT
    return ScanFailureKind.TRANSPORT_ERROR


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
                    category=RedactionCategory.ORG_PRIVATE,
                    rationale=finding.rationale,
                ),
            )
            continue
        if not _span_lies_inside(finding.start, finding.end, content):
            return ScanResult(failure=ScanFailureKind.SPANS_UNRESOLVABLE)
        hits.append(
            ScanHit(
                category=RedactionCategory.ORG_PRIVATE,
                start=finding.start,
                end=finding.end,
                rationale=finding.rationale,
            ),
        )
    hits.sort(key=lambda hit: hit.sort_key())
    return ScanResult(hits=tuple(hits))


def _span_lies_inside(start: int | None, end: int | None, content: str) -> bool:
    """Whether ``[start, end)`` is a real, non-empty span of *content*."""
    if start is None or end is None:
        return False
    return 0 <= start < end <= len(content)
