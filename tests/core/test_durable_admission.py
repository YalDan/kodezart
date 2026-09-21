"""Shared destination durability and non-redaction admission rules."""

import re

import pytest
from pydantic import ValidationError

from kodezart.adapters.outbound_admission import OutboundAdmission
from kodezart.composition.gating import build_outbound_gate
from kodezart.config.app import AppConfig
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.gating import (
    DESTINATION_DURABILITY,
    DESTINATION_SURFACE,
    ContentClass,
    DurabilityCategory,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    OutboundSurface,
    RedactionCategory,
    RepoVisibility,
    ScanCategory,
    ScanHit,
    SurfaceDurability,
    WriterShape,
    durability_of,
    surface_of,
)
from kodezart.types.domain.privacy import PrivateSurface
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.adapters.test_judgment_scanner import ScriptedAuditExecutor, audit_result
from tests.fakes import FakeContentJudgment
from tests.outbound import make_admission
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.test_linear_tool_roster import SOURCE_ROOT

#: The roster module itself declares every member, so it names them all and
#: could never tell an invented member from a real one.
ROSTER_MODULE = SOURCE_ROOT / "types" / "domain" / "gating.py"


@pytest.mark.parametrize(
    "url",
    [
        "https://linear.app/example-workspace/issue/EX-1/a-title",
        "https://linear.app/example-workspace/project/a-project-123",
        "https://linear.app/example-workspace/initiative/a-scope-456",
        "HTTPS://LINEAR.APP/example-workspace/issue/EX-2?tab=activity#comment-abc",
        "https://app.notion.com/p/0123456789abcdef0123456789abcdef",
        "https://app.notion.com/p/01234567-89ab-cdef-0123-456789abcdef?pvs=204",
    ],
)
async def test_unconfigured_native_urls_carry_no_implicit_private_workspace(url):
    content = f"Read <{url}> before work."
    decision = await (await configured_gate()).gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.categories == ()
    assert decision.content == content
    assert decision.content.startswith("Read <")
    assert decision.content.endswith("> before work.")


@pytest.mark.parametrize(
    "content",
    [
        "https://linear.app/",
        "https://linear.app/docs/issues",
        "https://linear.app.evil.invalid/example/issue/EX-1",
        "https://linearXapp/example/issue/EX-1",
        "https://app.notion.com.evil.invalid/p/0123456789abcdef0123456789abcdef",
        "https://appXnotionXcom/p/0123456789abcdef0123456789abcdef",
        "https://example.invalid/example/issue/EX-1",
        "example-workspace",
    ],
)
async def test_url_defaults_do_not_supply_workspace_name_patterns(content):
    decision = await (await configured_gate()).gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == content


@pytest.mark.parametrize("patterns", [[], ["synthetic-organisation"]])
def test_workspace_name_pattern_category_is_still_rejected(patterns):
    with pytest.raises(ValidationError):
        AppConfig(deny_patterns={RedactionCategory.ORG_PRIVATE: patterns})


def test_every_real_writer_has_a_durability_classification() -> None:
    """Adding a destination without deciding how it is read fails this gate."""
    assert set(DESTINATION_DURABILITY) == set(OutboundDestination)


def test_every_real_writer_has_a_surface_classification() -> None:
    """The surface map is total too, so a new member classifies both or neither."""
    assert set(DESTINATION_SURFACE) == set(OutboundDestination)


def naming_modules(destination: OutboundDestination) -> list[str]:
    """Every source file outside the roster that addresses *destination*."""
    pattern = re.compile(rf"\bOutboundDestination\.{destination.name}\b")
    return [
        str(path.relative_to(SOURCE_ROOT))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if path != ROSTER_MODULE and pattern.search(path.read_text(encoding="utf-8"))
    ]


@pytest.mark.parametrize(
    "destination", list(OutboundDestination), ids=lambda member: member.name
)
def test_every_destination_member_is_named_by_a_production_writer(
    destination: OutboundDestination,
) -> None:
    """A member whose writer does not exist is an invented value (KOD-484).

    Derived over the roster, so a member added here has to be addressed by
    some production module before it is a destination at all — which is also
    what forbids registering one for a surface nothing writes.
    """
    assert naming_modules(destination), f"{destination.name} names no writer"


def test_the_status_update_destination_is_named_by_the_terminal() -> None:
    """The control for the scan: the writer of the newest member is that lane."""
    assert "services/scope_terminal.py" in naming_modules(
        OutboundDestination.TRACKER_STATUS_UPDATE
    )


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.BRANCH_NAME,
        OutboundDestination.PR_TITLE,
        OutboundDestination.PR_BODY,
        OutboundDestination.ARTIFACT_TICKET_JSON,
        OutboundDestination.ARTIFACT_CRITERIA_JSON,
        OutboundDestination.TRACKER_DESCRIPTION,
        OutboundDestination.TRACKER_TITLE,
    ],
)
def test_current_descriptions_and_artifacts_are_durable(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.DURABLE


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.PR_COMMENT,
        OutboundDestination.TRACKER_COMMENT,
        OutboundDestination.TRACKER_STATUS_UPDATE,
        OutboundDestination.COMMIT_MESSAGE,
        OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY,
    ],
)
def test_appended_events_are_point_in_time(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.POINT_IN_TIME


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.TRACKER_COMMENT,
        OutboundDestination.TRACKER_STATUS_UPDATE,
        OutboundDestination.TRACKER_DESCRIPTION,
        OutboundDestination.TRACKER_TITLE,
        OutboundDestination.TRACKER_CLASSIFICATION,
    ],
)
def test_tracker_destinations_write_onto_the_tracker_surface(
    destination: OutboundDestination,
) -> None:
    """Every write of the coordination surface is classified as one."""
    assert surface_of(destination) is OutboundSurface.TRACKER


def test_a_write_without_a_destination_member_is_durable() -> None:
    assert durability_of(None) is SurfaceDurability.DURABLE


def test_scan_category_vocabularies_are_disjoint() -> None:
    """Every union member owns distinct wire values, so decoding is unambiguous."""
    from typing import get_args

    seen: set[str] = set()
    for vocabulary in get_args(ScanCategory.__value__):
        values = {member.value for member in vocabulary}
        assert values.isdisjoint(seen)
        seen.update(values)
    assert DurabilityCategory.OBJECT_COUNT.value == "object_count"
    assert DurabilityCategory.IDENTIFIER_ROSTER.value == "identifier_roster"


@pytest.mark.parametrize("category", list(DurabilityCategory))
async def test_aggregate_categories_block_without_a_redaction_option(
    category: DurabilityCategory,
) -> None:
    """The existing gate's non-redaction arm handles both added categories."""
    hit = ScanHit(category=category, start=0, end=8)
    gate = make_admission(FakeContentJudgment([hit]))
    decision = await gate.gate(
        content="3 issues remain",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.content == ""
    assert decision.categories == (category,)
    assert GateDecision.model_validate_json(decision.model_dump_json()) == decision


async def configured_gate(config: AppConfig | None = None) -> OutboundAdmission:
    return await build_outbound_gate(
        config=config or AppConfig(agentic_content_scanner_enabled=False),
        operation=None,
        executor=ScriptedAuditExecutor([audit_result([])]),
        prompts=load_registry(),
        skills=SkillsSelection(mode=SkillsMode.NONE),
        log=get_logger(__name__),
    )


async def test_aggregate_block_wins_over_an_earlier_redaction() -> None:
    content = "https://linear.app/private-example/issue/EX-3: 5 tickets remain"
    start = content.index("5 tickets")
    gate = make_admission(
        FakeContentJudgment(
            [
                ScanHit(
                    category=DurabilityCategory.OBJECT_COUNT,
                    start=start,
                    end=start + len("5 tickets"),
                )
            ]
        ),
        private_surface=PrivateSurface(workspaces={"linear.app": ["private-example"]}),
    )
    decision = await gate.gate(
        content=content,
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert set(decision.categories) == {
        RedactionCategory.TRACKER_URLS,
        DurabilityCategory.OBJECT_COUNT,
    }
    assert decision.content == ""


@pytest.mark.parametrize("destination", list(OutboundDestination))
async def test_single_identifier_is_a_reference_on_every_surface(
    destination: OutboundDestination,
) -> None:
    decision = await (await configured_gate()).gate(
        content="See ABC-42 for the details.",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=destination,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN


async def test_privacy_match_text_is_not_copied_into_the_blocked_error() -> None:
    secret = "ghp_" + "A" * 40
    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await gated_write(
            gate=await configured_gate(),
            log=get_logger(__name__),
            content=f"credential={secret}",
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
    assert secret not in str(excinfo.value)
    assert all(hit.matched_text is None for hit in excinfo.value.hits)
