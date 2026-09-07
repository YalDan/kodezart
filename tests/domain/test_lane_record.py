"""The lane record retains branch facts without another satisfaction carrier."""

import json

import pytest
from pydantic import ValidationError

from kodezart.domain.lane_record import render_lane_record
from kodezart.types.domain.branch import BranchAssociation, BranchRole, WorkRefRole
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.run_state import LaneCommit, LanePR, LaneRunState


def record_data() -> dict[str, object]:
    return {
        "laneKey": "lane:alpha",
        "branch": "ordinary-name",
        "branchUrl": "https://forge.example/branch/ordinary-name",
        "headSha": "head-full-identity",
        "pushedHeadSha": "head-full-identity",
        "commitsAhead": 2,
        "filesChanged": 3,
        "commits": [
            {"sha": "first-sha", "subject": "First change", "issueId": "EXT/42"},
            {
                "sha": "head-full-identity",
                "subject": "Second change\nwith Unicode: λ and ```",
                "issueId": "EXT/43",
            },
        ],
        "pr": {"url": "https://forge.example/pr/7", "number": 7, "state": "OPEN"},
        "associations": [
            {
                "branch": "ordinary-name",
                "role": "loop",
                "derivedFrom": "has-ralph-in-its-name",
                "runId": "run-current",
            },
            {
                "branch": "has-ralph-in-its-name",
                "role": "deliverable",
                "derivedFrom": None,
                "runId": "run-current",
            },
            {
                "branch": "reaped-ref",
                "role": "recovery",
                "derivedFrom": "ordinary-name",
                "runId": "run-current",
            },
            {
                "branch": "earlier-deliverable",
                "role": "deliverable",
                "derivedFrom": None,
                "runId": "run-earlier",
            },
        ],
    }


def test_record_and_association_have_only_the_declared_fields():
    assert set(LaneRunState.model_fields) == {
        "lane_key",
        "branch",
        "branch_url",
        "head_sha",
        "pushed_head_sha",
        "commits_ahead",
        "files_changed",
        "commits",
        "pr",
        "associations",
    }
    assert set(LaneCommit.model_fields) == {"sha", "subject", "issue_id"}
    assert set(LanePR.model_fields) == {"url", "number", "state"}
    assert set(BranchAssociation.model_fields) == {
        "branch",
        "role",
        "derived_from",
        "run_id",
    }
    assert {role.value for role in BranchRole} == {"deliverable", "loop", "recovery"}
    assert "iteration" in {role.value for role in WorkRefRole}


@pytest.mark.parametrize("remote_head", [None, "head-full-identity", "older-head"])
def test_three_remote_states_and_every_fact_survive_serialization(remote_head):
    data = {**record_data(), "pushedHeadSha": remote_head}
    record = LaneRunState.model_validate(data)
    assert json.loads(record.model_dump_json(by_alias=True)) == data
    assert LaneRunState.model_validate_json(record.model_dump_json()) == record
    assert record.pushed_head_sha == remote_head
    assert [row.issue_id for row in record.commits] == ["EXT/42", "EXT/43"]
    assert [row.subject for row in record.commits] == [
        "First change",
        "Second change\nwith Unicode: λ and ```",
    ]


def test_record_uses_explicit_roles_and_keeps_reaped_and_prior_run_associations():
    record = LaneRunState.model_validate(record_data())
    assert record.associations[0].role is BranchRole.LOOP
    assert record.associations[1].role is BranchRole.DELIVERABLE
    assert record.associations[2].branch == "reaped-ref"
    assert record.associations[3].run_id == "run-earlier"
    assert record.associations[0].derived_from == record.associations[1].branch


@pytest.mark.parametrize("field", ["commitsAhead", "filesChanged"])
def test_negative_observed_counts_are_refused(field):
    with pytest.raises(ValidationError):
        LaneRunState.model_validate({**record_data(), field: -1})


def test_observed_count_disagreement_remains_available_to_its_signal():
    record = LaneRunState.model_validate({**record_data(), "commitsAhead": 20})
    assert record.commits_ahead == 20
    assert len(record.commits) == 2


def test_record_and_each_nested_fact_are_frozen_and_closed():
    record = LaneRunState.model_validate(record_data())
    for model in (record, record.commits[0], record.pr, record.associations[0]):
        assert model is not None
        name = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, name, "replacement")
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(model).model_validate({**model.model_dump(), "satisfaction": True})


def test_caller_cannot_rewrite_record_by_clearing_original_input_lists():
    data = record_data()
    record = LaneRunState.model_validate(data)
    data["commits"].clear()
    data["associations"].clear()
    assert len(record.commits) == 2
    assert len(record.associations) == 4


def test_no_pr_remains_an_explicit_absence():
    data = record_data()
    del data["pr"]
    assert LaneRunState.model_validate(data).pr is None


def test_nonloop_current_branch_and_second_deliverable_in_one_run_refuse():
    data = record_data()
    with pytest.raises(ValidationError, match="LOOP"):
        LaneRunState.model_validate({**data, "branch": "has-ralph-in-its-name"})
    data["associations"].append(
        {
            "branch": "second-result",
            "role": "deliverable",
            "derivedFrom": None,
            "runId": "run-current",
        }
    )
    with pytest.raises(ValidationError, match="one DELIVERABLE"):
        LaneRunState.model_validate(data)


def test_several_loop_associations_in_one_run_remain_valid():
    data = record_data()
    data["associations"].append(
        {
            "branch": "fix-loop",
            "role": "loop",
            "derivedFrom": "has-ralph-in-its-name",
            "runId": "run-current",
        }
    )
    assert len(LaneRunState.model_validate(data).associations) == 5


def test_render_has_one_configured_marker_and_one_readable_fact_block():
    record = LaneRunState.model_validate(record_data())
    rendered = render_lane_record(
        record=record, marker_prefixes={"run_state": "fixture-record"}
    )
    assert rendered.startswith("[fixture-record:lane%3Aalpha]\n```json\n")
    payload = rendered.split("\n", 2)[2].removesuffix("\n```")
    assert json.loads(payload) == record_data()
    assert '"issueId": "EXT/43"' in rendered
    assert "crossOffs" not in rendered
    assert rendered == render_lane_record(
        record=record, marker_prefixes={"run_state": "fixture-record"}
    )


def test_render_does_not_guess_an_absent_configured_marker():
    with pytest.raises(OperationMemberAbsentError, match="run_state"):
        render_lane_record(
            record=LaneRunState.model_validate(record_data()), marker_prefixes={}
        )
