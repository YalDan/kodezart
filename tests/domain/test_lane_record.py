"""The lane record retains branch facts without another satisfaction carrier."""

import dataclasses
import json
from pathlib import Path
from typing import Literal, get_args, get_type_hints

import pytest
from pydantic import BaseModel, ValidationError, create_model

from kodezart.domain.errors import LaneRecordWriteError
from kodezart.domain.lane_record import (
    lane_record_body,
    next_lane_record,
    render_lane_record,
)
from kodezart.types.domain import run_state
from kodezart.types.domain.branch import BranchAssociation, BranchRole, WorkRefRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.run_state import (
    LaneBinding,
    LaneCommit,
    LanePR,
    LaneRunState,
)
from tests.identity_guards import construction_sites, model_value_sites

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
RECORD = "LaneRunState"


def binding() -> LaneBinding:
    return LaneBinding(
        lane_key="lane:alpha",
        loop_branch="ordinary-name",
        deliverable_branch="has-ralph-in-its-name",
        base_ref="trunk",
        repo_url="https://forge.example/repo",
        repo_path=None,
        run_id="run-current",
        visibility=RepoVisibility.PRIVATE,
    )


def changeset(*, commits: int = 2, files: int = 3) -> ChangesetDigest:
    return ChangesetDigest(
        file_paths=[f"file-{index}.py" for index in range(files)],
        commit_subjects=[f"Change {index}" for index in range(commits)],
        commit_count=commits,
    )


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


def test_optional_reference_absence_is_none_not_an_empty_string():
    data = record_data()
    with pytest.raises(ValidationError):
        LaneRunState.model_validate({**data, "pushedHeadSha": ""})
    data["associations"][0]["derivedFrom"] = ""
    with pytest.raises(ValidationError):
        LaneRunState.model_validate(data)


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
    payload = rendered.split("\n", 2)[2].split("\n```", 1)[0]
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


@pytest.mark.parametrize("remote_head", [None, "head-full-identity", "older-head"])
def test_every_record_ends_with_the_fixed_truthful_role_aware_reentry(remote_head):
    record = LaneRunState.model_validate(
        {**record_data(), "pushedHeadSha": remote_head}
    )
    rendered = render_lane_record(
        record=record, marker_prefixes={"run_state": "fixture-record"}
    )
    expected = """## Re-entry

Resume the branch identified by the LOOP role and the record's branch field.
When pushedHeadSha is present, that branch exists on the remote at the recorded
head; check out the existing branch. When pushedHeadSha is null, no remote copy
was recorded: recover the existing branch before continuing. Never mint a new
branch in place of a recorded association. Follow the explicit roles and
derivedFrom links to the deliverable, other loop and recovery branches; do not
infer their roles from their names. Associations survive reaping, so verify
current remote liveness before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work."""
    assert rendered.endswith("\n\n" + expected)
    assert rendered.count("## Re-entry") == 1


def test_the_marker_and_the_body_compose_the_whole_rendered_record():
    record = LaneRunState.model_validate(record_data())
    rendered = render_lane_record(
        record=record, marker_prefixes={"run_state": "fixture-record"}
    )
    assert rendered == "[fixture-record:lane%3Aalpha]\n" + lane_record_body(
        record=record
    )


def test_exactly_one_site_constructs_the_lane_run_state():
    sites = [
        (path, line)
        for path in SOURCE_ROOT.rglob("*.py")
        for line in construction_sites(path.read_text(), identity=RECORD)
    ]
    assert len(sites) == 1, sites
    assert sites[0][0] == SOURCE_ROOT / "domain" / "lane_record.py"
    owner = (SOURCE_ROOT / "domain" / "lane_record.py").read_text()
    second = f"{owner}\n{RECORD}(lane_key='second')\n"
    assert len(construction_sites(second, identity=RECORD)) == 2


def record_value_sites() -> dict[str, list[str]]:
    """Every place the source builds a lane record, and every place it parses one."""
    found: dict[str, list[str]] = {"build": [], "parse": []}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        sites = model_value_sites(path.read_text(), identity=RECORD)
        for form, functions in sites.items():
            found[form].extend(
                f"{path.relative_to(SOURCE_ROOT).as_posix()}::{function}"
                for function in functions
            )
    return found


def test_one_site_builds_the_lane_run_state_and_one_site_parses_it():
    assert record_value_sites() == {
        "build": ["domain/lane_record.py::next_lane_record"],
        "parse": ["domain/lane_record.py::parse_lane_record"],
    }


@pytest.mark.parametrize(
    "form",
    [
        f"{RECORD}(lane_key='second')",
        f"{RECORD}.model_validate({{'laneKey': 'second'}})",
        f"{RECORD}.model_validate_json('{{}}')",
        f"{RECORD}.model_construct(lane_key='second')",
        "prior.model_copy(update={'head_sha': sha})",
        "type(prior)(lane_key='second')",
    ],
)
def test_a_second_site_in_any_of_the_construction_forms_is_reported(form):
    owner = (SOURCE_ROOT / "domain" / "lane_record.py").read_text()
    poller = f"{owner}\n\ndef _poll(prior, sha):\n    return {form}\n"
    sites = model_value_sites(poller, identity=RECORD)
    assert "_poll" in sites["build"] + sites["parse"]


def test_a_module_that_does_not_hold_the_record_states_nothing_about_it():
    borrowed = "def _poll(prior, sha):\n    return prior.model_copy(update={})\n"
    assert model_value_sites(borrowed, identity=RECORD) == {"build": (), "parse": ()}


def test_next_record_appends_one_row_and_keeps_prior_associations():
    lane = binding()
    first = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    assert first.lane_key == lane.lane_key
    assert first.branch == lane.loop_branch
    assert first.head_sha == "a" * 40
    assert first.pushed_head_sha == "a" * 40
    assert first.branch_url == "https://forge.example/branch/ordinary-name"
    assert first.commits_ahead == 1
    assert first.files_changed == 1
    assert first.pr is None
    assert [row.sha for row in first.commits] == ["a" * 40]
    assert [(row.subject, row.issue_id) for row in first.commits] == [
        ("First change", lane.lane_key)
    ]
    assert [
        (item.branch, item.role, item.derived_from) for item in first.associations
    ] == [
        (lane.deliverable_branch, BranchRole.DELIVERABLE, lane.base_ref),
        (lane.loop_branch, BranchRole.LOOP, lane.deliverable_branch),
    ]
    assert {item.run_id for item in first.associations} == {lane.run_id}

    second = next_lane_record(
        prior=first,
        lane=lane,
        branch_url="https://forge.example/branch/renamed",
        head_sha="b" * 40,
        pushed_head_sha=None,
        changeset=changeset(commits=2, files=3),
        subject="Second change",
    )
    assert [row.sha for row in second.commits] == ["a" * 40, "b" * 40]
    assert second.associations == first.associations
    assert second.head_sha == "b" * 40
    assert second.branch_url == "https://forge.example/branch/renamed"
    assert second.pushed_head_sha is None
    assert second.commits_ahead == 2
    assert second.files_changed == 3


def test_the_pull_request_the_prior_record_carries_survives_the_next_commit():
    prior = LaneRunState.model_validate(record_data())
    lane = LaneBinding(
        lane_key=prior.lane_key,
        loop_branch=prior.branch,
        deliverable_branch="has-ralph-in-its-name",
        base_ref="trunk",
        repo_url="https://forge.example/repo",
        repo_path=None,
        run_id="run-fix",
        visibility=RepoVisibility.PRIVATE,
    )
    later = next_lane_record(
        prior=prior,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="d" * 40,
        pushed_head_sha=None,
        changeset=changeset(commits=3, files=2),
        subject="Third change",
    )
    assert prior.pr is not None
    assert later.pr == prior.pr


def test_a_run_rebound_to_another_deliverable_refuses_before_composing_a_record():
    lane = binding()
    first = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    rebound = LaneBinding(
        lane_key=lane.lane_key,
        loop_branch=lane.loop_branch,
        deliverable_branch="another-deliverable",
        base_ref=lane.base_ref,
        repo_url=lane.repo_url,
        repo_path=lane.repo_path,
        run_id=lane.run_id,
        visibility=lane.visibility,
    )
    with pytest.raises(LaneRecordWriteError) as refusal:
        next_lane_record(
            prior=first,
            lane=rebound,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="b" * 40,
            pushed_head_sha=None,
            changeset=changeset(commits=2, files=1),
            subject="Second change",
        )
    assert lane.deliverable_branch in str(refusal.value)
    assert "another-deliverable" in str(refusal.value)


def test_a_run_rebased_onto_another_base_refuses_before_composing_a_record():
    lane = binding()
    first = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    rebased = LaneBinding(
        lane_key=lane.lane_key,
        loop_branch=lane.loop_branch,
        deliverable_branch=lane.deliverable_branch,
        base_ref="another-base",
        repo_url=lane.repo_url,
        repo_path=lane.repo_path,
        run_id=lane.run_id,
        visibility=lane.visibility,
    )
    with pytest.raises(LaneRecordWriteError, match="another-base"):
        next_lane_record(
            prior=first,
            lane=rebased,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="b" * 40,
            pushed_head_sha=None,
            changeset=changeset(commits=2, files=1),
            subject="Second change",
        )


def test_a_head_that_returns_to_an_earlier_sha_is_recorded_as_its_own_act():
    lane = binding()
    record = None
    for head, subject in (
        ("a", "First change"),
        ("b", "Second change"),
        ("a", "Reset"),
    ):
        record = next_lane_record(
            prior=record,
            lane=lane,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha=head * 40,
            pushed_head_sha=None,
            changeset=changeset(commits=1, files=1),
            subject=subject,
        )
    assert record is not None
    assert [row.sha for row in record.commits] == ["a" * 40, "b" * 40, "a" * 40]
    assert [row.subject for row in record.commits] == [
        "First change",
        "Second change",
        "Reset",
    ]


def test_recording_the_same_head_twice_leaves_the_record_unchanged():
    lane = binding()
    first = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    assert (
        next_lane_record(
            prior=first,
            lane=lane,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="a" * 40,
            pushed_head_sha="a" * 40,
            changeset=changeset(commits=1, files=1),
            subject="First change",
        )
        == first
    )


def test_a_later_run_adds_its_own_association_pair_beside_the_first():
    lane = binding()
    first = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    remediation = LaneBinding(
        lane_key=lane.lane_key,
        loop_branch="second-loop",
        deliverable_branch=lane.deliverable_branch,
        base_ref=lane.base_ref,
        repo_url=lane.repo_url,
        repo_path=lane.repo_path,
        run_id="run-later",
        visibility=lane.visibility,
    )
    later = next_lane_record(
        prior=first,
        lane=remediation,
        branch_url="https://forge.example/branch/second-loop",
        head_sha="c" * 40,
        pushed_head_sha="c" * 40,
        changeset=changeset(commits=2, files=1),
        subject="Later change",
    )
    assert later.branch == "second-loop"
    assert later.associations[:2] == first.associations
    assert [
        (item.branch, item.role, item.run_id) for item in later.associations[2:]
    ] == [
        (lane.deliverable_branch, BranchRole.DELIVERABLE, "run-later"),
        ("second-loop", BranchRole.LOOP, "run-later"),
    ]
    assert [row.sha for row in later.commits] == ["a" * 40, "c" * 40]


def declared_fields(owner: type) -> dict[str, object]:
    """Every declared field of a run-state type, model or dataclass alike.

    Read through ``get_type_hints`` rather than off the raw annotation, so a
    quoted annotation — and every annotation in a module with postponed
    evaluation — is the type it names rather than the string that spells it.
    """
    hints = get_type_hints(owner, include_extras=True)
    if issubclass(owner, BaseModel):
        return {name: hints[name] for name in owner.model_fields}
    return {field.name: hints[field.name] for field in dataclasses.fields(owner)}


def annotation_types(annotation: object) -> set[object]:
    """The annotation itself and every type it is composed of.

    A ``Literal`` carries values where other annotations carry types, so a
    literal ``True`` or ``False`` among its arguments is reported as ``bool``:
    a flag spelled that way is still a flag.
    """
    if isinstance(annotation, bool):
        return {bool}
    arguments = get_args(annotation)
    return {annotation}.union(
        *(annotation_types(argument) for argument in arguments), set()
    )


def reached_types(roots: list[type]) -> dict[type, dict[str, object]]:
    """Every model or dataclass the *roots* reach through their own fields.

    The surface is walked out of the annotations themselves: whatever type a
    field names, wherever it is declared, is visited and its own fields are
    read the same way, so nothing enters this guard as a named file.
    """
    reached: dict[type, dict[str, object]] = {}
    pending = list(roots)
    while pending:
        owner = pending.pop()
        if owner in reached:
            continue
        reached[owner] = declared_fields(owner)
        pending.extend(
            component
            for annotation in reached[owner].values()
            for component in annotation_types(annotation)
            if isinstance(component, type)
            and (
                issubclass(component, BaseModel) or dataclasses.is_dataclass(component)
            )
        )
    return reached


def boolean_fields(reached: dict[type, dict[str, object]]) -> list[str]:
    """Every declared field of the walked types whose annotation admits a bool."""
    return sorted(
        f"{owner.__name__}.{name}"
        for owner, fields in reached.items()
        for name, annotation in fields.items()
        if bool in annotation_types(annotation)
    )


def run_state_types() -> list[type]:
    return [
        member
        for member in vars(run_state).values()
        if isinstance(member, type) and member.__module__ == run_state.__name__
    ]


def test_no_type_the_lane_record_reaches_declares_a_boolean_field():
    reached = reached_types([LaneRunState, *run_state_types()])
    assert set(run_state_types()) <= set(reached)
    assert BranchAssociation in reached, "the walk stops short of the nested facts"
    assert boolean_fields(reached) == []


@pytest.mark.parametrize(
    "annotation", [bool, bool | None, tuple[bool, ...], Literal[True, False]]
)
def test_the_boolean_guard_sees_a_flag_however_it_is_wrapped(annotation):
    flag = create_model("Flag", pushed=(annotation, ...))
    assert [
        name
        for name, declared in declared_fields(flag).items()
        if bool in annotation_types(declared)
    ] == ["pushed"]


def test_the_walk_reports_a_flag_on_a_model_declared_somewhere_else():
    elsewhere = create_model("Elsewhere", pushed=(bool, False))
    root = create_model("Root", nested=(list[elsewhere], ...))
    assert boolean_fields(reached_types([root])) == ["Elsewhere.pushed"]


@dataclasses.dataclass(frozen=True)
class PlainFlag:
    pushed: bool = False


@dataclasses.dataclass(frozen=True)
class QuotedFlag:
    pushed: "bool" = False


@pytest.mark.parametrize("owner", [PlainFlag, QuotedFlag])
def test_the_walk_reports_a_dataclass_flag_however_its_annotation_is_spelled(owner):
    assert boolean_fields(reached_types([owner])) == [f"{owner.__name__}.pushed"]
