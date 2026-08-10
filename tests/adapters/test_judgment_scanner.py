"""The judgment scanner: fail-closed, routing, determinism, conformance, boot.

Four properties, each one a way the mechanism can be wrong without any test
noticing:

* **F** — every way of having no answer is BLOCKED and names its kind.  A
  scanner that cannot answer is a blocked payload, never an absent one, and
  "did not answer" is never collapsed into "said it is clean".
* **R** — routing asserted by CALL COUNT, because the affordability of the
  whole design is a claim about how often the model runs.
* **D** — within one run a payload gets one answer and pays for one call.
* **S** — one conformance suite both adapters pass, which is what keeps the
  widened port from quietly becoming a judgment-only port.

The audit session is driven by a scripted executor throughout.  What is
under test here is the MECHANISM around a verdict; the model is not, and a
test that needed the model to be right would be measuring the wrong thing.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kodezart.adapters.agent_content_scanner import AgentContentScanner
from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.gating import outbound_scanners
from kodezart.core.config import AppConfig
from kodezart.core.content_classification import ContentClassifier
from kodezart.core.errors import ContentScannerBootError
from kodezart.core.protocols import ContentScanner
from kodezart.types.domain.agent import AgentEvent, RateLimitWarningEvent, ResultEvent
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanFailureKind,
    WriterShape,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import FakeContentScanner
from tests.prompts.test_prompt_wiring import load_registry

# A SYNTHETIC organisation. A fixture built from the real description would
# publish exactly what this mechanism exists to withhold, and passing under a
# synthetic one is what shows the mechanism generalises rather than that one
# string was memorised.
FIXTURE_PRIVATE_SURFACE = (
    "Workspace segments, customer identities, member handles and unreleased "
    "capabilities belonging to the fictional operation 'quarry-works'."
)

NO_SKILLS = SkillsSelection(mode=SkillsMode.NONE, allowlist=())

PROSE = "The quarry-works board says the pricing pilot slipped again."


def audit_result(
    findings: list[dict[str, object]] | None,
    *,
    is_error: bool = False,
    subtype: str = "success",
) -> ResultEvent:
    """A terminal event carrying a structured audit verdict, or an error."""
    return ResultEvent(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="audit",
        structured_output=None if findings is None else {"findings": findings},
    )


class ScriptedAuditExecutor:
    """``AgentExecutor`` replaying scripted audit sessions, counting calls."""

    def __init__(
        self,
        events: list[AgentEvent],
        *,
        raises: Exception | None = None,
    ) -> None:
        self._events = events
        self._raises = raises
        self.calls: list[dict[str, object]] = []

    def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "allowed_tools": allowed_tools,
                "session_id": session_id,
            },
        )
        return self._emit()

    async def _emit(self) -> AsyncIterator[AgentEvent]:
        if self._raises is not None:
            raise self._raises
        for event in self._events:
            yield event


def scanner_for(
    executor: ScriptedAuditExecutor,
    *,
    private_surface: str | None = FIXTURE_PRIVATE_SURFACE,
    retry_max_attempts: int = 1,
    timeout_seconds: float = 30.0,
) -> AgentContentScanner:
    """A judgment scanner over the REAL registry and template."""
    bindings: dict[str, object] = {}
    if private_surface is not None:
        bindings["private_surface"] = private_surface
    return AgentContentScanner(
        executor=executor,
        prompts=load_registry(bindings=bindings),
        neutral_cwd="/tmp/kodezart-content-audit-test",
        skills=NO_SKILLS,
        retry_max_attempts=retry_max_attempts,
        retry_initial_interval=0.01,
        timeout_seconds=timeout_seconds,
    )


def gate_over(scanners: list[ContentScanner]) -> PatternOutboundContentGate:
    """A gate over *scanners* with the shipped category verdicts."""
    return PatternOutboundContentGate(
        scanners=scanners,
        verdicts=AppConfig().deny_pattern_verdicts,
    )


# ---------------------------------------------------------------------------
# F — fail-closed, one case per ScanFailureKind member
# ---------------------------------------------------------------------------


class SleepingExecutor(ScriptedAuditExecutor):
    """An audit session that never terminates within the bound."""

    async def _emit(self) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(10)
        yield audit_result([])


async def test_a_session_that_never_answers_is_timeout() -> None:
    """F/TIMEOUT."""
    scanner = scanner_for(SleepingExecutor([]), timeout_seconds=0.01)
    result = await scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    assert result.failure is ScanFailureKind.TIMEOUT


async def test_a_refused_session_is_refusal() -> None:
    """F/REFUSAL."""
    executor = ScriptedAuditExecutor(
        [audit_result(None, is_error=True, subtype="refusal")],
    )
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.REFUSAL


async def test_output_that_is_not_the_verdict_shape_is_malformed() -> None:
    """F/MALFORMED_VERDICT."""
    executor = ScriptedAuditExecutor(
        [audit_result([{"rationale": "", "start": 0, "end": 1}])],
    )
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.MALFORMED_VERDICT


async def test_a_rejected_rate_limit_is_rate_limited() -> None:
    """F/RATE_LIMITED."""
    executor = ScriptedAuditExecutor(
        [RateLimitWarningEvent(status="rejected"), audit_result([])],
    )
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.RATE_LIMITED


async def test_a_transport_failure_is_transport_error() -> None:
    """F/TRANSPORT_ERROR."""
    executor = ScriptedAuditExecutor([], raises=OSError("connection reset"))
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.TRANSPORT_ERROR


async def test_a_session_with_no_terminal_event_is_empty_response() -> None:
    """F/EMPTY_RESPONSE."""
    result = await scanner_for(ScriptedAuditExecutor([])).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.EMPTY_RESPONSE


async def test_a_span_outside_the_payload_is_spans_unresolvable() -> None:
    """F/SPANS_UNRESOLVABLE — the whole result, never a dropped finding."""
    executor = ScriptedAuditExecutor(
        [
            audit_result(
                [
                    {"start": 4, "end": 16, "rationale": "workspace segment"},
                    {"start": 9000, "end": 9001, "rationale": "off the end"},
                ],
            ),
        ],
    )
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.SPANS_UNRESOLVABLE
    assert result.hits == ()


async def test_an_exhausted_budget_is_budget_exhausted() -> None:
    """F/BUDGET_EXHAUSTED."""
    executor = ScriptedAuditExecutor(
        [audit_result(None, is_error=True, subtype="budget_exceeded")],
    )
    result = await scanner_for(executor).scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is ScanFailureKind.BUDGET_EXHAUSTED


async def test_a_scanner_without_its_configuration_is_not_configured() -> None:
    """F/NOT_CONFIGURED — registered with no private surface to judge against."""
    executor = ScriptedAuditExecutor([audit_result([])])
    scanner = scanner_for(executor, private_surface=None)
    result = await scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    assert result.failure is ScanFailureKind.NOT_CONFIGURED
    assert executor.calls == []


@pytest.mark.parametrize("kind", list(ScanFailureKind))
async def test_every_failure_kind_blocks_and_names_itself(
    kind: ScanFailureKind,
) -> None:
    """No member yields CLEAN, none yields REDACTED, none is skipped."""
    gate = gate_over([FakeContentScanner(failure=kind)])
    decision = await gate.gate(
        content=PROSE,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.failure is kind
    assert decision.content == ""


async def test_did_not_answer_and_said_clean_are_different_states() -> None:
    """The three-state discipline, asserted as an inequality of observables."""
    silent = await gate_over(
        [FakeContentScanner(failure=ScanFailureKind.TIMEOUT)],
    ).gate(
        content=PROSE,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    clean = await gate_over([FakeContentScanner(hits=[])]).gate(
        content=PROSE,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert silent.verdict is not clean.verdict
    assert silent.failure is not None
    assert clean.failure is None


# ---------------------------------------------------------------------------
# R — routing, asserted by call count
# ---------------------------------------------------------------------------


async def gate_once(
    scanner: FakeContentScanner,
    *,
    content: str,
    destination: OutboundDestination,
    visibility: RepoVisibility = RepoVisibility.PUBLIC,
    shape: WriterShape = WriterShape.PROSE,
) -> None:
    """Gate one payload through a judgment-routed double."""
    await gate_over([scanner]).gate(
        content=content,
        visibility=visibility,
        shape=shape,
        destination=destination,
    )


async def test_a_structured_evaluator_cadence_payload_costs_nothing() -> None:
    """R: zero calls — by classification, never by exemption."""
    scanner = FakeContentScanner(hits=[])
    await gate_once(
        scanner,
        content='{"criterion": "AC-1", "passed": true, "sha": "a1b2c3d"}',
        destination=OutboundDestination.PR_COMMENT,
    )
    assert scanner.calls == []


async def test_an_authored_prose_pull_request_body_costs_exactly_one() -> None:
    """R: one call."""
    scanner = FakeContentScanner(hits=[])
    await gate_once(scanner, content=PROSE, destination=OutboundDestination.PR_BODY)
    assert len(scanner.calls) == 1


async def test_a_branch_name_is_audited_despite_being_an_identifier() -> None:
    """R: one call per run — the routing rule is frequency x prose origin."""
    scanner = FakeContentScanner(hits=[])
    await gate_once(
        scanner,
        content="kodezart/quarry-works-pricing-pilot",
        destination=OutboundDestination.BRANCH_NAME,
        shape=WriterShape.IDENTIFIER,
    )
    assert len(scanner.calls) == 1


@pytest.mark.parametrize("destination", list(OutboundDestination))
async def test_a_private_target_costs_nothing_at_every_destination(
    destination: OutboundDestination,
) -> None:
    """R: zero calls — the gate returns before any scanner runs."""
    scanner = FakeContentScanner(hits=[])
    await gate_once(
        scanner,
        content=PROSE,
        destination=destination,
        visibility=RepoVisibility.PRIVATE,
    )
    assert scanner.calls == []


async def test_the_repository_surface_is_out_of_scope_for_the_judgment_path() -> None:
    """R: a commit message is carried in history, not published at write time."""
    scanner = FakeContentScanner(hits=[])
    await gate_once(
        scanner,
        content=PROSE,
        destination=OutboundDestination.COMMIT_MESSAGE,
    )
    assert scanner.calls == []


async def test_a_deterministic_block_short_circuits_the_model_call() -> None:
    """R: a credential is caught with no network call, ordering preserved."""
    judgment = FakeContentScanner(hits=[])
    gate = gate_over(
        [
            RegexContentScanner(
                patterns={
                    RedactionCategory.CREDENTIALS: [r"\bgh[posu]_[A-Za-z0-9]{36,}"]
                },
            ),
            judgment,
        ],
    )
    decision = await gate.gate(
        content="deploy with ghp_" + "a" * 36 + " and tell nobody",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert judgment.calls == []


# ---------------------------------------------------------------------------
# D — determinism within one run
# ---------------------------------------------------------------------------


async def test_the_same_payload_triple_is_answered_once_per_run() -> None:
    """D: one invocation, one verdict, no second cost."""
    scanner = FakeContentScanner(hits=[])
    gate = PatternOutboundContentGate(
        scanners=[scanner],
        verdicts=AppConfig().deny_pattern_verdicts,
        fragment_digest="digest-a",
    )
    first = await gate.gate(
        content=PROSE,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    second = await gate.gate(
        content=PROSE,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert len(scanner.calls) == 1
    assert first == second


async def test_a_changed_fragment_digest_invalidates_the_answer() -> None:
    """D: the memo is keyed on the fragment, so a changed one re-invokes."""
    scanner = FakeContentScanner(hits=[])
    for digest in ("digest-a", "digest-b"):
        gate = PatternOutboundContentGate(
            scanners=[scanner],
            verdicts=AppConfig().deny_pattern_verdicts,
            fragment_digest=digest,
        )
        await gate.gate(
            content=PROSE,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
        )
    assert len(scanner.calls) == 2


async def test_a_changed_destination_is_a_different_question() -> None:
    """D: the memo key carries the destination, never the payload alone."""
    scanner = FakeContentScanner(hits=[])
    gate = gate_over([scanner])
    for destination in (
        OutboundDestination.PR_BODY,
        OutboundDestination.TRACKER_COMMENT,
    ):
        await gate.gate(
            content=PROSE,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=destination,
        )
    assert len(scanner.calls) == 2


# ---------------------------------------------------------------------------
# S — conformance, one suite BOTH adapters pass
# ---------------------------------------------------------------------------


def conformance_adapters() -> list[ContentScanner]:
    """Every registered ``ContentScanner`` implementation, both of them.

    The judgment adapter is driven by a scripted session reporting a real
    span; what conforms or fails to conform here is the port contract, not
    the verdict.
    """
    executor = ScriptedAuditExecutor(
        [audit_result([{"start": 4, "end": 16, "rationale": "workspace segment"}])],
    )
    return [
        RegexContentScanner(
            patterns={RedactionCategory.TRACKER_URLS: [r"quarry-works"]},
        ),
        scanner_for(executor),
    ]


@pytest.mark.parametrize("scanner", conformance_adapters())
async def test_every_returned_span_lies_inside_the_payload(
    scanner: ContentScanner,
) -> None:
    """S: a span that cannot be excised is not a hit."""
    result = await scanner.scan(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert result.failure is None
    for hit in result.hits:
        assert hit.start is not None
        assert hit.end is not None
        assert 0 <= hit.start < hit.end <= len(PROSE)


@pytest.mark.parametrize("scanner", conformance_adapters())
async def test_no_scanner_raises_across_the_port(scanner: ContentScanner) -> None:
    """S: a failure is a typed value, never an exception at the seam."""
    for destination in OutboundDestination:
        result = await scanner.scan(content="", destination=destination)
        assert result is not None


@pytest.mark.parametrize("scanner", conformance_adapters())
async def test_a_private_visibility_call_invokes_no_scanner_at_all(
    scanner: ContentScanner,
) -> None:
    """S: the gate returns CLEAN before the ordered list is entered."""
    decision = await gate_over([scanner]).gate(
        content=PROSE,
        visibility=RepoVisibility.PRIVATE,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == PROSE


def test_the_deterministic_adapter_declares_unconditional_routing() -> None:
    """S: the port widening did not become a judgment-only port."""
    scanner = RegexContentScanner(patterns={})
    for destination in OutboundDestination:
        for content_class in ContentClass:
            assert scanner.routing.applies(
                destination=destination,
                content_class=content_class,
            )


def test_the_classifier_routes_the_two_shapes_the_gate_distinguishes() -> None:
    """The routing input itself, read through its own implementation."""
    classifier = ContentClassifier()
    assert classifier.classify("a1b2c3d") is ContentClass.STRUCTURED
    assert classifier.classify(PROSE) is ContentClass.AUTHORED_PROSE


# ---------------------------------------------------------------------------
# Boot — three states, none silent
# ---------------------------------------------------------------------------


def operation_with(private_surface: str | None) -> OperationConfig:
    """The shipped example operation config, with its private surface set."""
    root = Path(__file__).resolve().parents[2]
    config = load_operation_config(root / "docs" / "operation.example.toml")
    return config.model_copy(update={"private_surface": private_surface})


def boot_scanners(
    *,
    enabled: bool,
    private_surface: str | None,
) -> tuple[list[ContentScanner], str]:
    """Resolve the ordered scanner list exactly as the lifespan does."""
    return outbound_scanners(
        config=AppConfig(agentic_content_scanner_enabled=enabled),
        operation=operation_with(private_surface),
        executor=ScriptedAuditExecutor([]),
        prompts=load_registry(),
        skills=NO_SKILLS,
    )


def test_disabled_registers_the_deterministic_scanner_alone() -> None:
    """State 1: the mechanism ships, the policy is operator configuration."""
    scanners, digest = boot_scanners(
        enabled=False,
        private_surface=FIXTURE_PRIVATE_SURFACE,
    )
    assert [type(scanner).__name__ for scanner in scanners] == ["RegexContentScanner"]
    assert digest == ""


def test_enabled_with_a_description_registers_the_judgment_scanner_second() -> None:
    """State 2: deterministic FIRST, and that ordering is load-bearing."""
    scanners, digest = boot_scanners(
        enabled=True,
        private_surface=FIXTURE_PRIVATE_SURFACE,
    )
    assert [type(scanner).__name__ for scanner in scanners] == [
        "RegexContentScanner",
        "AgentContentScanner",
    ]
    assert digest


@pytest.mark.parametrize("private_surface", [None, "", "   \n "])
def test_enabled_without_a_description_aborts_boot(private_surface: str | None) -> None:
    """State 3: NOT_CONFIGURED never degrades into a quietly missing scanner."""
    with pytest.raises(ContentScannerBootError) as excinfo:
        boot_scanners(enabled=True, private_surface=private_surface)
    assert excinfo.value.missing == "OperationConfig.private_surface"
