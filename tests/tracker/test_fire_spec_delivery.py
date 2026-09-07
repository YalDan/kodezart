"""Captured tracker subjects reach a real description session without a draft."""

import ast
import inspect

import pytest
from pydantic import ValidationError

from kodezart.chains import delivery_coordinator
from kodezart.domain.errors import DeliveryContextError
from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.delivery import DeliveryContext
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.prompts import PromptKey
from tests.chains.test_delivery_runtime import context, deliver, setup
from tests.fakes import FakeMcpIssue, make_criteria
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.tracker.conftest import FIXTURE_NOW, fixture_server

SUBJECT = "subject/42"
CHILD = "condition/café"
BODY = "**Outcome:** Keep literal tracker text.\r\n\n{{unbound}} <data> café"
CHECK = "**Check:** Preserve whitespace and own identity.\n\n**Evidence:** recorded"


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[SUBJECT] = FakeMcpIssue(
        id=SUBJECT, description=BODY, updated_at=FIXTURE_NOW
    )
    server.issues[CHILD] = FakeMcpIssue(
        id=CHILD,
        parent_id=SUBJECT,
        labels=["acceptance-condition"],
        description=CHECK,
    )
    return server


async def tracked_context(tracker):
    spec = await tracker.read_fire_spec(issue_key=SUBJECT)
    rows = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    values = context().model_dump(exclude={"spec", "criteria"})
    return DeliveryContext.model_validate({**values, "spec": spec, "criteria": rows})


@pytest.mark.parametrize("family", [V5_SET, OPUS_SET])
async def test_actual_tracker_read_reaches_delivery_session_without_draft(
    tracker, tracker_writes, family, monkeypatch
):
    facts = await tracked_context(tracker)
    assert isinstance(facts.spec, TrackerSpec)
    before = tracker_writes()

    def forbidden_draft(*_args, **_kwargs):
        raise AssertionError("a tracker subject cannot become an authored draft")

    monkeypatch.setattr(TicketDraftOutput, "__init__", forbidden_draft)
    monkeypatch.setattr(TicketDraftOutput, "model_validate", forbidden_draft)
    facts = DeliveryContext.model_validate_json(facts.model_dump_json())
    fixture = setup(family=family)
    observed = await deliver(fixture.coordinator, facts=facts)
    assert observed.outcome is WorkflowOutcome.ci_passed
    assert tracker_writes() == before
    variables = fixture.prompts.variables_for(PromptKey.PR_DESCRIPTION)
    assert len(variables) == 1
    assert variables[0]["task_md"] == BODY
    assert variables[0]["acceptance_criteria"] == list(facts.criteria)
    prompt = fixture.runner.calls[0]["prompt"]
    assert BODY in prompt
    assert f"{CHILD} (owning issue: {SUBJECT})\n{CHECK}" in prompt
    assert "Tracker issue: subject/42" in fixture.forge.calls[0]["body"]


async def test_delivery_retains_captured_subject_after_tracker_amendment(
    tracker, monkeypatch
):
    facts = await tracked_context(tracker)
    await tracker.update_issue(issue_key=SUBJECT, body="later tracker amendment")

    async def no_reread(**_kwargs):
        raise AssertionError("delivery must consume the supplied capture")

    monkeypatch.setattr(tracker, "read_issue", no_reread)
    monkeypatch.setattr(tracker, "read_fire_spec", no_reread)
    fixture = setup()
    await deliver(fixture.coordinator, facts=facts)
    assert fixture.prompts.variables_for(PromptKey.PR_DESCRIPTION)[0]["task_md"] == BODY
    assert facts.spec.read_at_version == FIXTURE_NOW.isoformat()


@pytest.mark.parametrize(
    "damage", ["empty", "legacy", "duplicate", "wrong-key", "parent", "label"]
)
async def test_tracker_source_refuses_unmatched_or_legacy_criteria(tracker, damage):
    facts = await tracked_context(tracker)
    rows = list(facts.criteria)
    if damage == "empty":
        rows = []
    elif damage == "legacy":
        rows = make_criteria("a legacy criterion is not the tracker record")
    elif damage == "duplicate":
        rows *= 2
    else:
        field, value = {
            "wrong-key": ("issue_key", "other/1"),
            "parent": ("parent_key", "other-parent/1"),
            "label": ("issue_labels", frozenset()),
        }[damage]
        rows[0] = rows[0].model_copy(update={field: value})
    with pytest.raises(ValidationError, match="tracker delivery"):
        DeliveryContext.model_validate({**facts.model_dump(), "criteria": rows})


async def test_authored_source_refuses_tracker_rows(tracker):
    facts = await tracked_context(tracker)
    with pytest.raises(ValidationError, match="authored delivery"):
        context(criteria=facts.criteria)


async def test_repeated_captured_reference_is_not_two_criterion_records(tracker):
    facts = await tracked_context(tracker)
    repeated = facts.spec.model_copy(update={"criteria": (CHILD, CHILD)})
    with pytest.raises(ValidationError, match="match the captured spec"):
        DeliveryContext.model_validate(
            {
                **facts.model_dump(),
                "spec": repeated,
                "criteria": (*facts.criteria, *facts.criteria),
            }
        )


async def test_criterion_rows_preserve_the_captured_reference_order(tracker):
    facts = await tracked_context(tracker)
    later = facts.criteria[0].model_copy(update={"issue_key": "another/record"})
    spec = facts.spec.model_copy(update={"criteria": (CHILD, later.issue_key)})
    values = {
        **facts.model_dump(),
        "spec": spec,
        "criteria": (*facts.criteria, later),
    }
    assert DeliveryContext.model_validate(values).criteria == (*facts.criteria, later)
    with pytest.raises(ValidationError, match="match the captured spec"):
        DeliveryContext.model_validate(
            {**values, "criteria": tuple(reversed(values["criteria"]))}
        )


async def test_foreign_captured_subject_refuses_before_delivery_side_effects(tracker):
    facts = await tracked_context(tracker)
    spec = facts.spec.model_copy(update={"subject": "other/42"})
    rows = tuple(
        row.model_copy(update={"parent_key": "other/42"}) for row in facts.criteria
    )
    facts = DeliveryContext.model_validate(
        {**facts.model_dump(), "spec": spec, "criteria": rows}
    )
    fixture = setup()
    with pytest.raises(DeliveryContextError, match="FIRE run identity"):
        await deliver(fixture.coordinator, facts=facts)
    assert fixture.runner.calls == fixture.forge.calls == fixture.monitor.calls == []
    assert fixture.gate.calls == fixture.query.calls == []


def test_delivery_never_constructs_or_parses_a_ticket_draft():
    tree = ast.parse(inspect.getsource(delivery_coordinator))
    assert not {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "TicketDraftOutput"
    }
