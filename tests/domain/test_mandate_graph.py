"""Recorded identity and membership comparisons have paired quiet controls."""

import json

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.mandate_graph import (
    RULINGS_BOUND,
    rulings_outpace_closures,
    structural_write_uncrosses_milestone,
)
from kodezart.types.domain.mandate_graph import (
    IssueSupersession,
    LaneGraphSnapshot,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssuePriority, TrackerIssue, WorkflowStateKind
from tests.tracker.conftest import FIXTURE_NOW

SUBJECT = AlarmSubject(kind=AlarmSubjectKind.LANE, scope_key="scope", lane_key="lane")
MILESTONE = ScopeRef(kind=ScopeKind.MILESTONE, key="milestone")


def reading(value: object, source: str = "lane-record") -> AlarmReading:
    return AlarmReading(source_ref=source, value=json.dumps(value))


def ruling(
    identity: str, author: str = "machine", issue: str = "FIRE"
) -> dict[str, str]:
    return {"rulingId": identity, "authoredBy": author, "issueKey": issue}


def ruling_snapshot(
    rows: list[dict[str, str]], lane: str = "lane"
) -> dict[str, object]:
    return {"laneKey": lane, "issueKeys": ["FIRE"], "rulings": rows}


def ruling_inputs(
    *,
    rows: list[dict[str, str]] | None = None,
    baseline: list[dict[str, str]] | None = None,
    closed: tuple[str, ...] = (),
    bound: int = 1,
) -> tuple[AlarmReading, ...]:
    return (
        reading(ruling_snapshot(baseline or [])),
        reading(
            ruling_snapshot(rows if rows is not None else [ruling("a"), ruling("b")])
        ),
        reading(["criterion/open"]),
        reading(closed),
        reading(bound, RULINGS_BOUND),
    )


def count_alarm(inputs: tuple[AlarmReading, ...]) -> RunAlarm | None:
    return rulings_outpace_closures(
        subject=SUBJECT, readings=inputs, raised_at_sha="sha", raised_by="holder"
    )


def issue(
    key: str,
    state: WorkflowStateKind = WorkflowStateKind.COMPLETED,
    *,
    parent: str | None = None,
    milestone: str | None = None,
) -> TrackerIssue:
    return TrackerIssue(
        issue_key=key,
        title=key,
        body="body",
        priority=IssuePriority.NONE,
        state_name=state.value,
        state_kind=state,
        queue_states=frozenset(),
        team_key=None,
        created_at=FIXTURE_NOW,
        updated_at=FIXTURE_NOW,
        url=f"https://tracker.invalid/{key}",
        parent_key=parent,
        milestone_key=milestone,
    )


def graph(
    *,
    fire_state: WorkflowStateKind = WorkflowStateKind.COMPLETED,
    children: tuple[TrackerIssue, ...] = (),
    members: tuple[TrackerIssue, ...] = (),
    supersessions: tuple[IssueSupersession, ...] = (),
) -> LaneGraphSnapshot:
    fire = issue("FIRE", fire_state, milestone=MILESTONE.key)
    return LaneGraphSnapshot(
        lane_key="lane",
        fire_key="FIRE",
        milestone=MILESTONE,
        subtree=(fire, *children),
        milestone_members=(fire, *members),
        supersessions=supersessions,
    )


def graph_reading(snapshot: LaneGraphSnapshot) -> AlarmReading:
    return AlarmReading(source_ref="lane-graph", value=snapshot.model_dump_json())


def graph_alarm(before: LaneGraphSnapshot, after: LaneGraphSnapshot) -> RunAlarm | None:
    return structural_write_uncrosses_milestone(
        subject=SUBJECT,
        readings=(graph_reading(before), graph_reading(after)),
        raised_at_sha="sha",
        raised_by="holder",
    )


def test_count_uses_distinct_identity_and_preserves_replay() -> None:
    inputs = ruling_inputs(rows=[ruling("a"), ruling("a"), ruling("b")])
    alarm = count_alarm(inputs)
    assert alarm is not None
    assert alarm.signal is AlarmSignal.RULINGS_OUTPACE_CLOSURES
    assert alarm.bound is not None
    assert alarm.bound.config_field == RULINGS_BOUND
    assert alarm.bound.configured_value == 1
    assert alarm.bound.observed_value == 2
    assert alarm.readings == inputs
    restored = RunAlarm.model_validate_json(alarm.model_dump_json())
    assert count_alarm(restored.readings) == alarm


@pytest.mark.parametrize("count", range(5))
@pytest.mark.parametrize("bound", range(5))
def test_strict_count_boundary(count: int, bound: int) -> None:
    alarm = count_alarm(
        ruling_inputs(rows=[ruling(str(i)) for i in range(count)], bound=bound)
    )
    assert (alarm is not None) is (count > bound)


@pytest.mark.parametrize("closed", [(), ("other",), ("newly-closed",)])
def test_unrelated_closure_does_not_reset_window(closed: tuple[str, ...]) -> None:
    assert count_alarm(ruling_inputs(closed=closed)) is not None


def test_closing_previously_open_identity_resets_window() -> None:
    assert count_alarm(ruling_inputs(closed=("criterion/open",))) is None


def test_prior_rulings_and_principal_rulings_do_not_inflate_growth() -> None:
    assert (
        count_alarm(
            ruling_inputs(
                baseline=[ruling("old"), ruling("amended", "principal")],
                rows=[
                    ruling("old"),
                    ruling("amended"),
                    ruling("human", "principal"),
                    ruling("new"),
                ],
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "rows",
    [[ruling("same"), ruling("same", "principal")], [ruling("x", issue="FOREIGN")]],
)
def test_conflicting_or_foreign_ruling_refuses(rows: list[dict[str, str]]) -> None:
    with pytest.raises(RunShapeReadError):
        count_alarm(ruling_inputs(rows=rows))


@pytest.mark.parametrize("value", [None, 1, True, {}, "human", ""])
def test_required_author_is_a_closed_recorded_field(value: object) -> None:
    row: dict[str, object] = dict(ruling("x"))
    row["authoredBy"] = value
    inputs = list(ruling_inputs())
    inputs[1] = reading({"laneKey": "lane", "issueKeys": ["FIRE"], "rulings": [row]})
    with pytest.raises(RunShapeReadError):
        count_alarm(tuple(inputs))


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("raw", ["null", "{}", "not-json"])
def test_every_ruling_input_is_required_even_when_closure_is_visible(
    index: int, raw: str
) -> None:
    inputs = list(ruling_inputs(closed=("criterion/open",)))
    inputs[index] = inputs[index].model_copy(update={"value": raw})
    with pytest.raises(RunShapeReadError):
        count_alarm(tuple(inputs))


@pytest.mark.parametrize("index", range(1, 5))
def test_ruling_sources_and_configuration_name_are_exact(index: int) -> None:
    inputs = list(ruling_inputs())
    inputs[index] = inputs[index].model_copy(update={"source_ref": "other"})
    with pytest.raises(RunShapeReadError):
        count_alarm(tuple(inputs))


@pytest.mark.parametrize("state", list(WorkflowStateKind))
def test_open_new_child_under_completed_fire_is_structural(
    state: WorkflowStateKind,
) -> None:
    after = graph(children=(issue("CHILD", state, parent="FIRE"),))
    alarm = graph_alarm(graph(), after)
    assert (alarm is not None) is (state is not WorkflowStateKind.COMPLETED)
    if alarm is not None:
        assert alarm.signal is AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE
        assert alarm.bound is None
        restored = RunAlarm.model_validate_json(alarm.model_dump_json())
        assert restored == alarm
        assert (
            structural_write_uncrosses_milestone(
                subject=restored.subject,
                readings=restored.readings,
                raised_at_sha=restored.raised_at_sha,
                raised_by=restored.raised_by,
            )
            == alarm
        )


@pytest.mark.parametrize(
    "state",
    [
        WorkflowStateKind.UNSTARTED,
        WorkflowStateKind.STARTED,
        WorkflowStateKind.CANCELED,
    ],
)
def test_same_child_under_noncompleted_fire_stays_quiet(
    state: WorkflowStateKind,
) -> None:
    assert (
        graph_alarm(
            graph(fire_state=state),
            graph(
                fire_state=state,
                children=(issue("CHILD", WorkflowStateKind.STARTED, parent="FIRE"),),
            ),
        )
        is None
    )


def test_milestone_membership_also_counts_and_state_only_change_does_not() -> None:
    open_member = issue("MEMBER", WorkflowStateKind.STARTED, milestone=MILESTONE.key)
    assert graph_alarm(graph(), graph(members=(open_member,))) is not None
    old_member = issue("MEMBER", milestone=MILESTONE.key)
    assert (
        graph_alarm(graph(members=(old_member,)), graph(members=(open_member,))) is None
    )


def test_nested_child_and_cross_container_subtree_membership() -> None:
    parent = issue("PARENT", parent="FIRE", milestone="elsewhere")
    child = issue("CHILD", WorkflowStateKind.STARTED, parent="PARENT")
    assert (
        graph_alarm(graph(children=(parent,)), graph(children=(parent, child)))
        is not None
    )


def test_prior_open_member_means_container_was_not_crossed() -> None:
    existing = issue("OLD", WorkflowStateKind.STARTED, milestone=MILESTONE.key)
    new = issue("NEW", WorkflowStateKind.STARTED, parent="FIRE")
    assert (
        graph_alarm(
            graph(members=(existing,)), graph(members=(existing,), children=(new,))
        )
        is None
    )


@pytest.mark.parametrize("superseded", [False, True])
def test_canceled_prior_member_requires_explicit_supersession(superseded: bool) -> None:
    old = issue("OLD", WorkflowStateKind.CANCELED, parent="FIRE")
    refs = (
        (IssueSupersession(issue_key="OLD", source_ref="ruling/ref"),)
        if superseded
        else ()
    )
    before = graph(children=(old,), supersessions=refs)
    after = graph(
        children=(old, issue("NEW", WorkflowStateKind.STARTED, parent="FIRE")),
        supersessions=refs,
    )
    assert (graph_alarm(before, after) is not None) is superseded


def test_new_canceled_member_with_supersession_does_not_uncross() -> None:
    child = issue("CANCELED", WorkflowStateKind.CANCELED, parent="FIRE")
    refs = (IssueSupersession(issue_key="CANCELED", source_ref="recorded/successor"),)
    assert graph_alarm(graph(), graph(children=(child,), supersessions=refs)) is None


@pytest.mark.parametrize("parent", [None, "MISSING", "CHILD"])
def test_unrooted_or_cyclic_subtree_is_not_a_clean_read(parent: str | None) -> None:
    with pytest.raises(RunShapeReadError):
        graph_alarm(graph(), graph(children=(issue("CHILD", parent=parent),)))


def test_conflicting_shared_member_versions_refuse() -> None:
    child = issue("CHILD", parent="FIRE", milestone=MILESTONE.key)
    changed = child.model_copy(update={"body": "changed between reads"})
    with pytest.raises(RunShapeReadError):
        graph_alarm(graph(), graph(children=(child,), members=(changed,)))


@pytest.mark.parametrize("field", ["lane_key", "fire_key", "milestone"])
def test_cross_graph_identity_is_exact(field: str) -> None:
    after = graph().model_copy(
        update={
            field: ScopeRef(kind=ScopeKind.MILESTONE, key="other")
            if field == "milestone"
            else "other"
        }
    )
    with pytest.raises(RunShapeReadError):
        graph_alarm(graph(), after)


def test_snapshot_is_closed_and_frozen() -> None:
    snapshot = graph()
    with pytest.raises(ValidationError):
        snapshot.fire_key = "OTHER"
    with pytest.raises(ValidationError):
        LaneGraphSnapshot.model_validate({**snapshot.model_dump(), "crossed": True})


def test_ruling_identity_cannot_change_owning_issue_between_snapshots() -> None:
    inputs = list(ruling_inputs(baseline=[ruling("a")]))
    inputs[1] = reading(
        {
            "laneKey": "lane",
            "issueKeys": ["OTHER"],
            "rulings": [ruling("a", issue="OTHER")],
        }
    )
    with pytest.raises(RunShapeReadError, match="owning issue"):
        count_alarm(tuple(inputs))


@pytest.mark.parametrize("field", ["subtree", "milestone_members"])
def test_duplicate_membership_identity_refuses(field: str) -> None:
    current = graph()
    values = getattr(current, field)
    current = current.model_copy(update={field: (*values, values[0])})
    with pytest.raises(RunShapeReadError, match="repeats"):
        graph_alarm(graph(), current)


def test_foreign_milestone_member_refuses() -> None:
    with pytest.raises(RunShapeReadError, match="foreign milestone"):
        graph_alarm(graph(), graph(members=(issue("OTHER", milestone="elsewhere"),)))


@pytest.mark.parametrize("kind", [AlarmSubjectKind.SCOPE, AlarmSubjectKind.ISSUE])
def test_both_predicates_require_the_lane_subject(kind: AlarmSubjectKind) -> None:
    subject = AlarmSubject(
        kind=kind,
        scope_key="scope",
        issue_id="FIRE" if kind is AlarmSubjectKind.ISSUE else None,
    )
    with pytest.raises(RunShapeReadError):
        rulings_outpace_closures(
            subject=subject,
            readings=ruling_inputs(),
            raised_at_sha="sha",
            raised_by="holder",
        )
    with pytest.raises(RunShapeReadError):
        structural_write_uncrosses_milestone(
            subject=subject,
            readings=(graph_reading(graph()), graph_reading(graph())),
            raised_at_sha="sha",
            raised_by="holder",
        )


def test_ruling_bound_is_environment_configured_and_nonnegative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KODEZART_RUN_ALARM_MAX_RULINGS_WITHOUT_CLOSURE", "3")
    assert AppConfig().run_alarm_max_rulings_without_closure == 3
    with pytest.raises(ValidationError):
        AppConfig(run_alarm_max_rulings_without_closure=-1)
