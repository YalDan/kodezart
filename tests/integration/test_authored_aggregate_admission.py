"""Authored tracker aggregates reach the actual PR writer's fresh judgment."""

import pytest

from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.logging import get_logger
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.gating import (
    ContentClass,
    DurabilityCategory,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanFailureKind,
    WriterShape,
)
from tests.adapters.test_judgment_scanner import audit_result
from tests.chains.test_outbound_gating import make_engine, run_engine
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakePRCreator,
    FakeTicketGenerator,
    FakeVisibilityResolver,
    make_ticket_draft,
)
from tests.integration.test_private_reference_writes import (
    DescriptionExecutor,
    operation_with_facts,
)

COUNT_CLAIM = "The tracker contains a dozen unfinished issues."
ROSTER_CLAIM = "Tracker roster: issue:alpha, issue:beta, issue:gamma."


class RecordedTextJudge:
    """Return a controlled judgment only for the exact text under examination."""

    def __init__(self, claim, category, *, damage=None):
        self.claim = claim
        self.category = category
        self.damage = damage
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["prompt"]
        if "<<<PAYLOAD\n" in prompt:
            payload = prompt.split("<<<PAYLOAD\n", 1)[1].split("\nPAYLOAD", 1)[0]
        else:
            payload = prompt.split("<content>\n", 1)[1].split("\n</content>", 1)[0]
        start = payload.find(self.claim)
        findings = []
        if self.category is not None and start >= 0:
            findings.append(
                {
                    "category": self.category.value,
                    "start": start,
                    "end": start + len(self.claim),
                    "rationale": "This states the tracker's changing aggregate state.",
                }
            )
        if findings and self.damage == "unlocated":
            findings[0].pop("start")
            findings[0].pop("end")
        elif findings and self.damage == "outside":
            findings[0]["end"] = len(payload) + 1
        elif findings and self.damage == "unknown_category":
            findings[0]["category"] = "new_category"
        elif findings and self.damage == "missing_category":
            findings[0].pop("category")
        yield audit_result([]).model_copy(
            update={"structured_output": {"findings": findings}}
        )


@pytest.fixture(params=["anthropic_v5", "claude-opus"], autouse=True)
def actual_prompt_set(monkeypatch, request):
    monkeypatch.setenv("KODEZART_PROMPT_SET", request.param)


async def gate_with_judge(tmp_path, judge, *, privacy=False, legacy_roster_minimum=3):
    config = AppConfig(
        content_audit_working_dir=str(tmp_path),
        agentic_content_scanner_enabled=privacy,
        aggregate_identifier_roster_min_length=legacy_roster_minimum,
    )
    operation = operation_with_facts() if privacy else None
    log = get_logger(__name__)
    prompts = await boot_prompts(config=config, operation=operation, log=log)
    return await build_outbound_gate(
        config=config,
        operation=operation,
        executor=judge,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        log=log,
    )


@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
@pytest.mark.parametrize(
    ("claim", "category"),
    [
        (COUNT_CLAIM, DurabilityCategory.OBJECT_COUNT),
        (ROSTER_CLAIM, DurabilityCategory.IDENTIFIER_ROSTER),
    ],
)
async def test_actual_pr_blocks_authored_aggregate_with_shipped_policy(
    tmp_path, visibility, claim, category
):
    judge = RecordedTextJudge(claim, category)
    gate = await gate_with_judge(tmp_path, judge)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=gate,
        visibility_resolver=FakeVisibilityResolver(visibility),
        executor=DescriptionExecutor(claim),
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert creator.calls == []
    assert caught.value.categories == (category.value,)
    (hit,) = caught.value.hits
    assert hit.matched_text == claim
    assert hit.start == 0 and hit.end == len(claim)
    assert judge.calls
    assert all(call["allowed_tools"] == [] for call in judge.calls)
    assert all(call.get("session_id") is None for call in judge.calls)


@pytest.mark.parametrize(
    "body",
    ["3 tests passed.", "12 files changed.", "2 commits implement the fix."],
)
async def test_actual_pr_preserves_ordinary_numeric_prose(tmp_path, body):
    judge = RecordedTextJudge(body, None)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        executor=DescriptionExecutor(body),
    )
    await run_engine(engine)
    (created,) = creator.calls
    assert body in created["body"]


async def test_private_pr_preserves_the_existing_no_session_fast_path(tmp_path):
    judge = RecordedTextJudge(COUNT_CLAIM, DurabilityCategory.OBJECT_COUNT)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PRIVATE),
        executor=DescriptionExecutor(COUNT_CLAIM),
    )
    await run_engine(engine)
    (created,) = creator.calls
    assert COUNT_CLAIM in created["body"]
    assert judge.calls == []


@pytest.mark.parametrize("category", list(DurabilityCategory))
async def test_same_authored_claim_is_allowed_at_the_point_in_time_boundary(
    tmp_path, category
):
    body = COUNT_CLAIM if category is DurabilityCategory.OBJECT_COUNT else ROSTER_CLAIM
    judge = RecordedTextJudge(body, category)
    gate = await gate_with_judge(tmp_path, judge)
    for destination, expected in [
        (OutboundDestination.PR_BODY, GateVerdict.BLOCKED),
        (OutboundDestination.TRACKER_COMMENT, GateVerdict.CLEAN),
    ]:
        decision = await gate.gate(
            content=body,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=destination,
            content_class=ContentClass.AUTHORED,
        )
        assert decision.verdict is expected
        if expected is GateVerdict.CLEAN:
            assert decision.content == body
    assert len(judge.calls) == 1


class CriteriaTextExecutor(FakeAgentExecutor):
    def __init__(self, text):
        super().__init__(events=[])
        self.text = text

    async def stream(self, **kwargs):
        async for event in super().stream(**kwargs):
            if isinstance(event, ResultEvent) and event.structured_output is not None:
                criteria = event.structured_output.get("criteria")
                if isinstance(criteria, list):
                    event = event.model_copy(
                        update={
                            "structured_output": {
                                **event.structured_output,
                                "criteria": [
                                    {**row, "text": self.text} for row in criteria
                                ],
                            }
                        }
                    )
            yield event


@pytest.mark.parametrize("artifact", ["ticket", "criteria"])
@pytest.mark.parametrize("category", list(DurabilityCategory))
async def test_authored_json_leaves_are_judged_before_actual_artifact_persistence(
    tmp_path, artifact, category
):
    claim = COUNT_CLAIM if category is DurabilityCategory.OBJECT_COUNT else ROSTER_CLAIM
    judge = RecordedTextJudge(claim, category)
    persister = FakeArtifactPersister()
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        artifact_persister=persister,
        ticket_generator=FakeTicketGenerator(make_ticket_draft(summary=claim))
        if artifact == "ticket"
        else None,
        executor=CriteriaTextExecutor(claim) if artifact == "criteria" else None,
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert caught.value.writer == (
        OutboundDestination.ARTIFACT_TICKET_JSON.value
        if artifact == "ticket"
        else OutboundDestination.ARTIFACT_CRITERIA_JSON.value
    )
    assert caught.value.categories == (category.value,)
    (hit,) = caught.value.hits
    assert hit.matched_text == claim
    assert creator.calls == []
    assert not any(
        claim in content for row in persister.artifacts for content in row.values()
    )
    assert len(persister.persist_calls) == (0 if artifact == "ticket" else 1)


@pytest.mark.parametrize("privacy", [False, True])
@pytest.mark.parametrize(
    ("damage", "failure"),
    [
        ("unlocated", None),
        ("outside", ScanFailureKind.SPANS_UNRESOLVABLE),
        ("unknown_category", ScanFailureKind.MALFORMED_VERDICT),
        ("missing_category", ScanFailureKind.MALFORMED_VERDICT),
    ],
)
async def test_actual_pr_cannot_publish_an_unusable_aggregate_judgment(
    tmp_path, damage, failure, privacy
):
    judge = RecordedTextJudge(
        COUNT_CLAIM, DurabilityCategory.OBJECT_COUNT, damage=damage
    )
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge, privacy=privacy),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.UNKNOWN),
        executor=DescriptionExecutor(COUNT_CLAIM),
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert caught.value.writer == OutboundDestination.PR_BODY.value
    assert caught.value.failure is failure
    assert creator.calls == []
    if failure is None:
        (hit,) = caught.value.hits
        assert hit.category is DurabilityCategory.OBJECT_COUNT
        assert not hit.has_span and hit.matched_text is None


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (DurabilityCategory.OBJECT_COUNT, GateVerdict.CLEAN),
        (RedactionCategory.ORG_PRIVATE, GateVerdict.REDACTED),
    ],
)
async def test_point_in_time_aggregate_permission_does_not_skip_private_prose(
    tmp_path, category, expected
):
    judge = RecordedTextJudge(COUNT_CLAIM, category)
    gate = await gate_with_judge(tmp_path, judge, privacy=True)
    decision = await gate.gate(
        content=COUNT_CLAIM,
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is expected
    assert len(judge.calls) == 1
    assert "Private customer identities" in judge.calls[0]["prompt"]
    if expected is GateVerdict.CLEAN:
        assert decision.content == COUNT_CLAIM


async def test_shipped_credential_check_blocks_before_the_new_mandatory_session(
    tmp_path,
):
    credential = "ghp_" + "X" * 40
    judge = RecordedTextJudge(credential, None)
    gate = await gate_with_judge(tmp_path, judge)
    decision = await gate.gate(
        content=credential,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert judge.calls == []


async def test_single_public_reference_and_two_references_remain_ordinary_text(
    tmp_path,
):
    for body in ["See issue:alpha.", "Compare issue:alpha with issue:beta."]:
        judge = RecordedTextJudge(body, None)
        creator = FakePRCreator()
        engine = make_engine(
            pr_creator=creator,
            gate=await gate_with_judge(tmp_path, judge),
            visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
            executor=DescriptionExecutor(body),
        )
        await run_engine(engine)
        (created,) = creator.calls
        assert body in created["body"]
        assert any(body in call["prompt"] for call in judge.calls)


@pytest.mark.parametrize("legacy_minimum", [2, 9])
async def test_authored_roster_policy_does_not_take_the_legacy_pattern_override(
    tmp_path, legacy_minimum
):
    judge = RecordedTextJudge(ROSTER_CLAIM, DurabilityCategory.IDENTIFIER_ROSTER)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(
            tmp_path, judge, legacy_roster_minimum=legacy_minimum
        ),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.PUBLIC),
        executor=DescriptionExecutor(ROSTER_CLAIM),
    )
    with pytest.raises(OutboundContentBlockedError):
        await run_engine(engine)
    assert creator.calls == []
    assert judge.calls
    assert all("at least\n3 references" in call["prompt"] for call in judge.calls)


async def test_derived_technical_values_keep_the_existing_no_session_route(tmp_path):
    judge = RecordedTextJudge("3 tests", None)
    gate = await gate_with_judge(tmp_path, judge)
    decision = await gate.gate(
        content="3 tests",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.ARTIFACT_CRITERIA_JSON,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == "3 tests"
    assert judge.calls == []
