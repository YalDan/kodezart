"""Every refutation the sweep produces carries a mandate verdict (KOD-516).

The range is the verdict-bearing fields of the sweep's observation: every
field whose type holds a model declaring a verdict, found at any depth of
that type (inside an optional, a tuple or a root model).  Each such arm is
either completed by its mandate-completed report or stands beside the
reason its hunt could not run; the observation refuses to be built any
other way, and that refusal is the sweep's own completeness assertion.
Which arms bear a verdict is read off the observation's own field types, so
an arm added later is under the rule the moment it exists rather than when
somebody remembers to list it.
"""

from collections.abc import Iterator
from dataclasses import replace
from typing import get_args, get_type_hints

import pytest
from pydantic import BaseModel, RootModel

from kodezart.chains.audit_sweep import MANDATED_ARMS, AuditReadObservation
from kodezart.domain.errors import AgentSDKError
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_evidence import restamp_defect_class
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.tracker.test_audit_forge import forge
from tests.tracker.test_audit_forge_sweep import selected_operation, verifier
from tests.tracker.test_audit_sweep import BODY, CHILD, HEAD, LANE, PRIOR, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup
from tests.tracker.test_audit_terminal_mandate import terminal_ready

#: A commit the lane's stream records a grading at and no Evidence row names.
ELSEWHERE = "c" * 40


async def refuted_restamp(tracker, server, mode):
    """Seed a grading the criterion's row does not name, current or lapsed.

    ``current`` leaves the row at the head under review, so the claim arm
    runs as well; ``lapse`` puts the row behind the head on a Done
    criterion, so the observation leaves through the lapse return.
    """
    graded = HEAD if mode == "current" else PRIOR
    recorded = PRIOR if mode == "current" else ELSEWHERE
    await tracker.update_issue(issue_key=CHILD, body=BODY.replace(HEAD, graded))
    await state(
        tracker,
        server,
        CHILD,
        "In Review" if mode == "current" else "Done",
        WorkflowStateKind.STARTED if mode == "current" else WorkflowStateKind.COMPLETED,
    )
    await tracker.post_run_event(
        issue_key=ROOT,
        event=LaneRunEvent(
            kind=RunEventKind.CRITERION_REFUTED,
            lane_key=LANE,
            subject_key=CHILD,
            graded_sha=recorded,
        ),
    )


@pytest.mark.parametrize("mode", ["current", "lapse"])
async def test_a_refuted_restamp_carries_a_mandate_verdict(
    setup, tracker, server, tracker_writes, mode
):
    """A restamp the stream does not trace is hunted at the current head.

    The lapse case is the one the trace is most about and the one that
    leaves before the claim arm, so the report is built ahead of that
    return; the hunt writes nothing.
    """
    build, executor, *_ = setup
    await refuted_restamp(tracker, server, mode)
    before = tracker_writes()
    observation = (await build().run()).observations[0]

    trace = observation.restamp
    assert trace.verdict is AuditVerdict.REFUTED
    assert observation.restamp_report.trace == trace
    assert observation.restamp_report.mandate.verdict is AuditVerdict.REFUTED
    covered = observation.restamp_report.mandate.covered
    assert {item.surface.ref.key for item in covered} == {CHILD, ROOT}
    assert observation.evidence.is_lapse is (mode == "lapse")
    assert (observation.claim is None) is (mode == "lapse")
    (call,) = [
        call
        for call in executor.calls
        if call["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
    ]
    assert restamp_defect_class(trace) in call["prompt"]
    assert trace.reason in call["prompt"]
    assert f"<head_sha>{HEAD}</head_sha>" in call["prompt"]
    assert tracker_writes() == before


async def test_a_restamp_report_must_answer_the_trace_beside_it(setup, tracker, server):
    """A report completes the trace it was hunted for and no other.

    The observation's refuted trace is swapped for another refuted trace
    while its report stays, so the mandate verdict beside it answers a
    refutation the observation no longer carries: refused.
    """
    build, *_ = setup
    await refuted_restamp(tracker, server, "current")
    observation = (await build().run()).observations[0]
    other = observation.restamp.model_copy(
        update={"reason": f"{observation.restamp.reason} (another trace)"}
    )
    assert other.verdict is AuditVerdict.REFUTED
    assert other != observation.restamp

    with pytest.raises(ValueError, match="restamp report differs from the native"):
        replace(observation, restamp=other)


@pytest.mark.parametrize("mode", ["current", "lapse"])
async def test_a_failed_restamp_mandate_keeps_the_raw_trace_and_its_reason(
    setup, tracker, server, tracker_writes, mode
):
    """A restamp hunt that fails keeps the raw trace beside the reason.

    No report is built, so the refutation stands as the raw REFUTED trace
    with the reason its hunt could not run, which is what the runtime then
    refuses the subject on, lapse included; nothing is written.
    """
    build, executor, *_ = setup
    await refuted_restamp(tracker, server, mode)

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            raise AgentSDKError(
                "restamp mandate session unavailable", error_kind="fixture"
            )

    executor.during = during
    before = tracker_writes()
    observation = (await build().run()).observations[0]

    assert observation.restamp.verdict is AuditVerdict.REFUTED
    assert observation.restamp_report is None
    assert observation.unavailable_reason.startswith("AgentSDKError: ")
    assert "restamp mandate session unavailable" in observation.unavailable_reason
    assert observation.evidence.is_lapse is (mode == "lapse")
    assert tracker_writes() == before


async def refuted_arm(arm, setup, tracker, server):
    """A real sweep observation whose *arm* is REFUTED and complete."""
    build, executor, _, _, _, pr_states, _, operation = setup
    if arm == "terminal":
        await terminal_ready(tracker, server, pr_states)
        return (await build().run()).observations[1]
    if arm == "forge":
        await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)
        selected = selected_operation(operation)
        async with forge("fake", "work") as (ci, _):
            return (
                await build(
                    selected_op=selected,
                    selected_forge=verifier(tracker, selected, ci),
                ).run()
            ).observations[0]
    if arm == "restamp":
        await refuted_restamp(tracker, server, "current")
        return (await build().run()).observations[0]
    executor.verdict = "refuted"
    await state(tracker, server, CHILD, "In Review", WorkflowStateKind.STARTED)
    return (await build().run()).observations[0]


#: The completeness rule's rows, stated here independently of the table
#: under test: each verdict-bearing arm, the field its mandate verdict
#: completes it in, and the field naming why its hunt could not run.
ARMS = (
    ("terminal", "terminal_report", "unavailable_reason"),
    ("forge", "forge_report", "forge_unavailable_reason"),
    ("restamp", "restamp_report", "unavailable_reason"),
    ("evidence", "claim", "unavailable_reason"),
)


@pytest.mark.parametrize(
    ("arm", "completed", "reason"), ARMS, ids=[row[0] for row in ARMS]
)
async def test_a_refutation_without_its_mandate_fails_the_completeness_assertion(
    setup, tracker, server, arm, completed, reason
):
    """Drop the mandate verdict from a refutation the sweep produced: refused.

    The control keeps the same raw refutation beside the reason its hunt
    could not run, which is the shape a genuinely failed hunt takes, and is
    built; so the refusal is about the missing verdict and nothing else.
    The rows are this test's own, so a row the table loses or alters fails
    here rather than taking its case away with it.
    """
    assert set(MANDATED_ARMS) == set(ARMS)
    observation = await refuted_arm(arm, setup, tracker, server)
    assert getattr(observation, arm).verdict is AuditVerdict.REFUTED
    assert getattr(observation, completed) is not None

    with pytest.raises(
        ValueError, match=f"without its mandate verdict: {arm} requires"
    ):
        replace(observation, **{completed: None})
    control = replace(
        observation, **{completed: None, reason: "the mandate hunt could not run"}
    )
    assert getattr(control, arm) == getattr(observation, arm)


def verdict_models(hint: object) -> Iterator[type[BaseModel]]:
    """Every model declaring a verdict that *hint* holds, at any depth.

    A container or union is read through its arguments, and a root model
    through its root, so ``tuple[X, ...] | None`` holds ``X`` as surely as
    ``X`` does.
    """
    for member in get_args(hint):
        yield from verdict_models(member)
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        if issubclass(hint, RootModel):
            yield from verdict_models(hint.model_fields["root"].annotation)
        elif "verdict" in hint.model_fields:
            yield hint


def verdict_bearing_fields() -> frozenset[str]:
    """The observation's fields whose type holds a model declaring a verdict."""
    return frozenset(
        name
        for name, hint in get_type_hints(AuditReadObservation).items()
        if any(verdict_models(hint))
    )


def test_every_verdict_bearing_arm_is_under_the_completeness_assertion():
    """The rule's table ranges over exactly the arms that carry a verdict.

    Read off the field types rather than listed: a verdict-bearing arm added
    to the observation without a row would carry refutations the rule never
    sees, which is how the restamp trace once escaped it.
    """
    assert verdict_bearing_fields() == {"terminal", "forge", "restamp", "evidence"}
    assert verdict_bearing_fields() == {row[0] for row in MANDATED_ARMS}
