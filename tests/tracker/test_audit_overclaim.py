"""Native tracker snapshots feed every standing over-claim category."""

import pytest
from pydantic import ValidationError

from kodezart.chains.audit_overclaim import AuditOverclaimVerifier
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.domain.errors import AuditClaimReadError, AuditEvidenceReadError
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.types.domain.agent import AUDIT_OVERCLAIM_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_overclaim import OverclaimKind
from kodezart.types.domain.subagents import NO_SUBAGENTS
from tests.fakes import SUPPRESS_ALL_SKILLS
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_audit_sources import reader
from tests.tracker import test_audit_evidence as fixtures

setup = fixtures.setup
claim_setup = fixtures.claim_setup
server = fixtures.server


def payload(kind=None, **changes):
    rows = []
    for category in OverclaimKind:
        row = dict(
            kind=category.value,
            verdict="holds",
            evidence="No violating claim of this kind was observed.",
            recomputedValue=None,
            missingArtifact=None,
        )
        if category is kind:
            row.update(changes)
        rows.append(row)
    return dict(criterionKey=fixtures.CHILD, checks=rows, bytePairs=[])


def build(setup, tracker, *, set_name="anthropic_v5", source=None):
    _, runner, git, native_source, _, workspace, *_ = setup
    prompts = load_registry(default_set=set_name)
    return AuditOverclaimVerifier(
        sources=reader(setup, tracker),
        sessions=FreshAuditSession(
            git=git,
            workspace=workspace,
            runner=runner,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
        ),
        prompts=prompts,
        git=source or native_source,
    )


def answer(runner, output):
    runner._events = [
        fixtures.result_event(subtype="success", structured_output=output)
    ]


@pytest.mark.parametrize("kind", list(OverclaimKind))
@pytest.mark.parametrize("set_name", ["anthropic_v5", "claude-opus"])
async def test_each_standing_judgment_uses_current_native_source_and_fresh_session(
    setup, tracker, tracker_writes, kind, set_name
):
    _, runner, _, _, _, workspace, stored, *_ = setup
    fields = dict(
        verdict="refuted", evidence="The current source contradicts this claim."
    )
    if kind is OverclaimKind.AGGREGATE:
        fields["recomputedValue"] = "3"
    elif kind is OverclaimKind.COMPLETENESS:
        fields.update(
            verdict="unverifiable", missingArtifact="enumerable source roster"
        )
    answer(runner, payload(kind, **fields))
    before = tracker_writes()
    result = await build(setup, tracker, set_name=set_name).observe(fixtures.REQUEST)
    assert result.judgment.verdict.value == fields["verdict"]
    assert result.head_sha == fixtures.HEAD and result.graded_sha == fixtures.PRIOR
    assert result.record_ref == stored.comment_key and result.check == fixtures.CHECK
    assert tracker_writes() == before
    args = runner.arguments
    assert args["session_id"] is None
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["allowed_tools"] == EVAL_TOOLS and args["agents"] == NO_SUBAGENTS
    assert args["output_format"]["schema"] == AUDIT_OVERCLAIM_SCHEMA
    assert fixtures.HEAD in args["prompt"] and fixtures.PRIOR in args["prompt"]
    assert fixtures.CHECK in args["prompt"]
    assert (
        "AUTHOR_REASONING" not in args["prompt"] and fixtures.TEST not in args["prompt"]
    )
    assert all(kind.value in args["prompt"] for kind in OverclaimKind)
    assert workspace.calls[-1][0] == "release"


@pytest.mark.parametrize(
    "damage", ["foreign", "missing-category", "duplicate-category", "missing-witness"]
)
async def test_malformed_or_foreign_judgment_never_completes(setup, tracker, damage):
    output = payload()
    if damage == "foreign":
        output["criterionKey"] = "other/criterion"
    elif damage == "missing-category":
        output["checks"].pop()
    elif damage == "duplicate-category":
        output["checks"].append(output["checks"][0])
    else:
        output["checks"][1]["verdict"] = "unverifiable"
    answer(setup[1], output)
    with pytest.raises((ValidationError, AuditClaimReadError)):
        await build(setup, tracker).observe(fixtures.REQUEST)


@pytest.mark.parametrize("damage", ["criterion", "head", "record"])
async def test_changed_source_never_produces_a_completed_observation(
    setup, tracker, damage
):
    answer(setup[1], payload())

    async def during():
        if damage == "criterion":
            await tracker.update_issue(
                issue_key=fixtures.CHILD, body=fixtures.body(fixtures.HEAD)
            )
        elif damage == "head":
            setup[2]._remote_branch_shas["ordinary-name"] = fixtures.PRIOR
        else:
            await tracker.upsert_comment(
                target=fixtures.ROOT,
                marker=setup[6].body.splitlines()[0],
                body="No longer the source record.",
            )

    setup[1].during = during
    with pytest.raises(AuditEvidenceReadError):
        await build(setup, tracker).observe(fixtures.REQUEST)
