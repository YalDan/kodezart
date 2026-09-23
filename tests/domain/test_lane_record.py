"""The lane record retains branch facts without another satisfaction carrier."""

import dataclasses
import json
from collections import Counter
from pathlib import Path
from typing import (
    Literal,
    NamedTuple,
    NewType,
    TypeAliasType,
    TypedDict,
    get_args,
    get_type_hints,
)

import pytest
from pydantic import BaseModel, ValidationError, create_model

from kodezart.domain.errors import LaneRecordWriteError
from kodezart.domain.lane_record import (
    REENTRY_SECTION,
    lane_record_body,
    next_lane_record,
    parse_lane_record,
    render_lane_record,
)
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain import run_state
from kodezart.types.domain.branch import BranchAssociation, BranchRole, WorkRefRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criteria import CriterionId
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.run_state import (
    LaneBinding,
    LaneCommit,
    LanePR,
    LaneRunState,
)
from kodezart.types.domain.trajectory import IterationRecord
from tests.identity_guards import construction_sites, model_value_sites

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
RECORD = "LaneRunState"
#: The subject digest a binding carries, pinned on the record it composes.
DIGEST = "e" * 64


def binding(*, lane_key: str = "lane:alpha") -> LaneBinding:
    """The lane every case is stated over, by default the one lane.

    *lane_key* is a parameter so a case about the column that carries the
    delivered issue can be driven through a SECOND lane: with one lane in
    the module, a row that named its lane and a row that named a constant
    answer alike (KOD-681).
    """
    return LaneBinding(
        lane_key=lane_key,
        body_digest=DIGEST,
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
        "bodyDigest": DIGEST,
        # One deliverable, two loop and a recovery association for the current
        # run, and the earlier run's own deliverable beside them: the set the
        # enumerability and cardinality rules are stated over (KOD-703).
        "associations": [
            {
                "branch": "ordinary-name",
                "role": "loop",
                "derivedFrom": "has-ralph-in-its-name",
                "runId": "run-current",
            },
            {
                "branch": "fix-loop",
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
        "body_digest",
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


#: Every association the fixture carries, as the whole chain each one names.
RECORD_CHAINS = [
    ("ordinary-name", BranchRole.LOOP, "has-ralph-in-its-name", "run-current"),
    ("fix-loop", BranchRole.LOOP, "has-ralph-in-its-name", "run-current"),
    ("has-ralph-in-its-name", BranchRole.DELIVERABLE, None, "run-current"),
    ("reaped-ref", BranchRole.RECOVERY, "ordinary-name", "run-current"),
    ("earlier-deliverable", BranchRole.DELIVERABLE, None, "run-earlier"),
]


def association_chains(
    record: LaneRunState,
) -> list[tuple[str, BranchRole, str | None, str]]:
    """Each association as the whole chain it names, in the record's own order.

    Read as tuples rather than by index: what the record has to enumerate is
    the branch, its explicit role, what it was derived from and which run
    recorded it, and an index says none of those.
    """
    return [
        (item.branch, item.role, item.derived_from, item.run_id)
        for item in record.associations
    ]


def role_counts(record: LaneRunState, *, run_id: str) -> dict[BranchRole, int]:
    """How many associations one run carries at each role."""
    return dict(
        Counter(item.role for item in record.associations if item.run_id == run_id)
    )


def test_record_uses_explicit_roles_and_keeps_reaped_and_prior_run_associations():
    record = LaneRunState.model_validate(record_data())
    assert association_chains(record) == RECORD_CHAINS


def test_one_deliverable_two_loop_and_a_recovery_read_back_with_their_chains():
    """One run's whole association set reads back, chains intact (KOD-703).

    The roles are explicit and the derivation links are the record's own: a
    reaped recovery branch is still named with what it was derived from, and
    an earlier run's deliverable stands beside this run's rather than being
    replaced by it. Counting per role states the cardinality the model
    enforces — one deliverable per run, loop associations uncounted — over
    the same set the chains are read from.
    """
    record = LaneRunState.model_validate(record_data())
    assert role_counts(record, run_id="run-current") == {
        BranchRole.DELIVERABLE: 1,
        BranchRole.LOOP: 2,
        BranchRole.RECOVERY: 1,
    }
    assert role_counts(record, run_id="run-earlier") == {BranchRole.DELIVERABLE: 1}
    assert association_chains(record) == RECORD_CHAINS


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
    assert len(record.associations) == 5


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


@pytest.mark.parametrize(
    "digest", ["a" * 40, "a" * 65, "A" * 64, "g" * 64, "x" + "a" * 64]
)
def test_a_digest_of_another_shape_refuses_at_the_read(digest):
    """The recorded digest carries the shape of the algorithm that made it.

    A value of some other length, case or alphabet is not a digest of this
    lane's subject and would compare unequal at every later entry, refusing
    the lane for good; it refuses here, where the record is read.
    """
    with pytest.raises(ValidationError, match="bodyDigest"):
        LaneRunState.model_validate({**record_data(), "bodyDigest": digest})


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
            "branch": "third-loop",
            "role": "loop",
            "derivedFrom": "has-ralph-in-its-name",
            "runId": "run-current",
        }
    )
    assert len(LaneRunState.model_validate(data).associations) == 6


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

Resume at the record's last commit act, the sha of its final commits row, and
never at a remote tip the record does not name. Find the loop branch by the
LOOP role and the record's branch field. When the remote holds it at that sha,
check it out and continue it. When it stands anywhere else, a lane that still
owes criteria cuts a fresh loop branch from the record's last commit act and
keeps the old association, and the old branch stays where it stands; a lane
that owes nothing and carries no pull request is refused rather than delivered
from a branch standing elsewhere. When the remote no longer holds it, recover
it before continuing: never mint a new branch in place of a recorded
association. pushedHeadSha is where the remote held the loop branch when a
commit last observed it, not the head to resume at. Follow the explicit roles
and derivedFrom links to the deliverable, other loop and recovery branches; do
not infer their roles from their names. Associations survive reaping, so verify
current remote liveness before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work."""
    assert rendered.endswith("\n\n" + expected)
    assert rendered.count("## Re-entry") == 1


#: The re-entry section lane records were written with from 26dd593e
#: (2026-09-14) until cd4eb635 (2026-09-23), spelled here byte for byte: the
#: comments already on a board carry exactly these bytes, whatever the source
#: now calls them.
REENTRY_UNTIL_CD4EB635 = """## Re-entry

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

#: The re-entry section lane records were written with from cd4eb635
#: (2026-09-23) until it stated which lanes cut a fresh loop branch and which
#: are refused (2026-09-23), byte for byte.
REENTRY_FROM_CD4EB635 = """## Re-entry

Resume at the record's last commit act, the sha of its final commits row, and
never at a remote tip the record does not name. Find the loop branch by the
LOOP role and the record's branch field. When the remote holds it at that sha,
check it out and continue it. When it stands anywhere else, cut a fresh loop
branch from that sha and keep the old association; the old branch stays where
it stands. When the remote no longer holds it, recover it before continuing:
never mint a new branch in place of a recorded association. pushedHeadSha is
where the remote held the loop branch when a commit last observed it, not the
head to resume at. Follow the explicit roles and derivedFrom links to the
deliverable, other loop and recovery branches; do not infer their roles from
their names. Associations survive reaping, so verify current remote liveness
before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work."""

#: Every re-entry section a record on a board may still end with, other than
#: the one a writer renders today.
EARLIER_REENTRY_SECTIONS = (REENTRY_UNTIL_CD4EB635, REENTRY_FROM_CD4EB635)
#: Their names in a test id, in the same order.
EARLIER_REENTRY_IDS = ("until-cd4eb635", "from-cd4eb635")

PREFIXES = {"run_state": "fixture-record"}


def rendered_under(section: str) -> tuple[LaneRunState, str]:
    """A record, and its comment as a writer using *section* would have left it."""
    record = LaneRunState.model_validate(record_data())
    current = render_lane_record(record=record, marker_prefixes=PREFIXES)
    assert current.endswith("\n\n" + REENTRY_SECTION)
    return record, current.removesuffix(REENTRY_SECTION) + section


@pytest.mark.parametrize("section", EARLIER_REENTRY_SECTIONS, ids=EARLIER_REENTRY_IDS)
def test_a_record_written_under_an_earlier_reentry_section_reads_the_same(section):
    """A comment already on a board stays this lane's record when the text moves.

    The section is fixed text a writer appends, so a record written before it
    last changed ends with the earlier bytes. Refusing those would leave the
    lane unable to re-enter or be written again until someone edits the
    comment by hand; it is read as the same record instead.
    """
    record, earlier = rendered_under(section)
    assert section != REENTRY_SECTION
    assert (
        parse_lane_record(body=earlier, lane_key="lane:alpha", marker_prefixes=PREFIXES)
        == record
    )


def test_a_reentry_section_no_writer_ever_rendered_is_refused():
    """Only the exact texts a writer rendered are recognised, nothing near them."""
    _, invented = rendered_under(
        "## Re-entry\n\nResume at whatever tip the remote holds for the loop branch."
    )
    with pytest.raises(ValueError, match="fixed re-entry section is invalid"):
        parse_lane_record(
            body=invented, lane_key="lane:alpha", marker_prefixes=PREFIXES
        )
    # Not refused for its payload: the same body under the current section reads.
    _, current = rendered_under(REENTRY_SECTION)
    parse_lane_record(body=current, lane_key="lane:alpha", marker_prefixes=PREFIXES)


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


def source_tree() -> dict[str, str]:
    """The production tree, keyed the way a guard's report names a module."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text()
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


def test_one_site_builds_the_lane_run_state_and_one_site_parses_it():
    """The record has one writer, and the guard finds it without being told.

    What the guard covers: every module that imports or declares the record,
    reaches the record's own name through a module it imports, or imports
    something whose own annotations hand the value around — grown as a fixed
    point, so a reader that never names the type is scanned too. Inside such
    a module it counts the class call, a subclass of it, the parsing and
    constructing methods however they are reached, an adapter or partial
    built around the class, ``type(x)(...)`` and ``x.__class__(...)``, and a
    copy whose receiver its own function does not state another type for.

    Two build sites, both in the module that owns the value: the record a
    commit leaves behind and the same record carrying the pull request its
    delivery opened (KOD-843). A delivering step has no receipt and no
    changeset, so it cannot compose one through the first.

    What it does not see, and what review has to read from the code: a class
    or a method reached by runtime reflection — ``globals()[name]``,
    ``getattr(module, name)`` — since no annotation and no import names it;
    a value rebuilt field by field into some other model that renders the
    same bytes; and a module that holds the value only by receiving it as an
    unannotated argument from a holder.

    A receiver whose stated type is a base class of the record
    (``CamelCaseModel``, ``BaseModel``) or a type parameter of its function
    is excused by that statement and its copy is not seen: the statement is
    the receiver's own, and reading it as the record would excuse nothing
    the house rule of annotating with the concrete type does not already
    close. A carrier reached as an attribute of an imported module, rather
    than imported by name, is likewise not seen: the attribute route reaches
    the record's own name only.
    """
    assert model_value_sites(source_tree(), identity=RECORD) == {
        "build": (
            "domain/lane_record.py::next_lane_record",
            "domain/lane_record.py::record_with_pull_request",
        ),
        "parse": ("domain/lane_record.py::parse_lane_record",),
    }


@pytest.mark.parametrize(
    "form",
    [
        f"{RECORD}(lane_key='second')",
        f"{RECORD}.model_validate({{'laneKey': 'second'}})",
        f"{RECORD}.model_validate_json('{{}}')",
        f"{RECORD}.model_validate_strings({{'laneKey': 'second'}})",
        f"{RECORD}.model_construct(lane_key='second')",
        f"TypeAdapter({RECORD}).validate_python({{}})",
        f"partial({RECORD}.model_validate)",
        "prior.model_copy(update={'head_sha': sha})",
        "type(prior)(lane_key='second')",
        "prior.__class__(lane_key='second')",
    ],
)
def test_a_second_site_in_any_of_the_construction_forms_is_reported(form):
    sources = source_tree()
    sources["services/second_writer.py"] = (
        f"from kodezart.types.domain.run_state import {RECORD}\n"
        "\n"
        "def _poll(prior, sha):\n"
        f"    return {form}\n"
    )
    sites = model_value_sites(sources, identity=RECORD)
    assert "services/second_writer.py::_poll" in sites["build"] + sites["parse"]


@pytest.mark.parametrize(
    "module",
    [
        f"class {RECORD}:\n    pass\n\ndef _poll(prior, sha):\n    return {RECORD}()\n",
        "from kodezart.types.domain import run_state\n"
        "\n"
        "def _poll(prior, sha):\n"
        f"    return run_state.{RECORD}.model_validate({{}})\n",
        f"from kodezart.types.domain.run_state import {RECORD}\n"
        "\n"
        f"class _Polled({RECORD}):\n"
        "    pass\n",
    ],
)
def test_a_module_reaching_the_record_without_importing_the_name_is_scanned(module):
    """Importing the bare name is one way a module holds the value, not the way.

    A module that declares the class, or reaches it through the module it
    lives in, holds it as surely as one that imports it, and a guard keyed on
    the import would report nothing about either.
    """
    sources = source_tree()
    sources["services/second_writer.py"] = module
    sites = model_value_sites(sources, identity=RECORD)
    assert [
        place
        for place in sites["build"] + sites["parse"]
        if place.startswith("services/second_writer.py")
    ]


IMPORTS = (
    "from kodezart.types.domain.run_state import LaneRunState\n"
    "from kodezart.types.domain.tracker import TrackerComment\n"
)


@pytest.mark.parametrize(
    ("body", "reported"),
    [
        (
            # Another function's annotation of the same word excuses nothing.
            "class Sources:\n"
            "    comment: TrackerComment\n"
            "\n"
            "def _poll(prior: LaneRunState, sha):\n"
            "    comment = prior\n"
            "    return comment.model_copy(update={'head_sha': sha})\n",
            True,
        ),
        (
            "def _read() -> TrackerComment: ...\n"
            "\n"
            "def _poll(sha):\n"
            "    comment = _read()\n"
            "    return comment.model_copy(update={'body': sha})\n",
            False,
        ),
        (
            "def _read() -> LaneRunState: ...\n"
            "\n"
            "def _poll(sha):\n"
            "    record = _read()\n"
            "    return record.model_copy(update={'head_sha': sha})\n",
            True,
        ),
        (
            "def _read() -> TrackerComment: ...\n"
            "\n"
            "def _poll(prior: LaneRunState, sha):\n"
            "    comment: TrackerComment = _read()\n"
            "    comment = prior\n"
            "    return comment.model_copy(update={'head_sha': sha})\n",
            True,
        ),
        (
            # The receiver's own annotated assignment states its type, and
            # nothing else here does: the source it is read from states none.
            "def _read(): ...\n"
            "\n"
            "def _poll(sha):\n"
            "    comment: TrackerComment = _read()\n"
            "    return comment.model_copy(update={'body': sha})\n",
            False,
        ),
        (
            # A field's annotation on the class states the type of a ``self``
            # receiver, which is where the tree keeps a held value.
            "class Sources:\n"
            "    _comment: TrackerComment\n"
            "\n"
            "    def _poll(self, sha):\n"
            "        return self._comment.model_copy(update={'body': sha})\n",
            False,
        ),
        (
            # A nested function's own binding of the same word is its own.
            "def _read() -> TrackerComment: ...\n"
            "\n"
            "def _other(): ...\n"
            "\n"
            "def _poll(sha):\n"
            "    comment = _read()\n"
            "\n"
            "    def _again():\n"
            "        comment = _other()\n"
            "        return comment.body\n"
            "\n"
            "    _again()\n"
            "    return comment.model_copy(update={'body': sha})\n",
            False,
        ),
    ],
)
def test_a_receiver_is_excused_only_by_what_its_own_function_states(body, reported):
    """The excuse is the receiver's own type, and every type it is given.

    Read across the module, a common local name would be excused wherever any
    other function annotated that word with something else. Read as "any of
    its types is something else", a name holding the record on one line and
    another value on the next would be excused by the second. A nested
    function is not the same function: what it states about a word it binds
    itself says nothing about the outer receiver of that name.
    """
    sources = source_tree()
    sources["services/second_writer.py"] = IMPORTS + body
    sites = model_value_sites(sources, identity=RECORD)
    assert ("services/second_writer.py::_poll" in sites["build"]) is reported


def test_a_module_holding_the_record_only_through_its_reader_is_scanned():
    """The value reaches a module that never names its type, and it is scanned.

    The reader's own signature hands a record back, so importing the reader
    is holding the value: the copy idiom in such a module composes a complete
    second record, which is the exact shape the one-writer rule forbids.
    """
    sources = source_tree()
    sources["services/second_writer.py"] = (
        "from kodezart.services.lane_records import LaneRecordReader\n"
        "\n"
        "async def _poll(reader: LaneRecordReader, sha):\n"
        "    _, record = await reader.read(issue_key='i', lane_key='l')\n"
        "    return record.model_copy(update={'head_sha': sha})\n"
    )
    sites = model_value_sites(sources, identity=RECORD)
    assert "services/second_writer.py::_poll" in sites["build"]


@pytest.mark.parametrize(
    ("declaration", "carrier", "body"),
    [
        (
            f"from kodezart.types.domain.run_state import {RECORD}\n"
            "\n"
            f"def read_polled() -> '{RECORD}': ...\n",
            "read_polled",
            "def _poll(sha):\n"
            "    return read_polled().model_copy(update={'head_sha': sha})\n",
        ),
        (
            "from kodezart.types.domain import run_state\n"
            "\n"
            f"def read_polled() -> run_state.{RECORD}: ...\n",
            "read_polled",
            "def _poll(sha):\n"
            "    return read_polled().model_copy(update={'head_sha': sha})\n",
        ),
        (
            "from dataclasses import dataclass\n"
            f"from kodezart.types.domain.run_state import {RECORD}\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class PolledSources:\n"
            f"    record: {RECORD}\n",
            "PolledSources",
            "def _poll(sources: PolledSources, sha):\n"
            "    return sources.record.model_copy(update={'head_sha': sha})\n",
        ),
    ],
)
def test_a_carrier_is_found_however_its_own_annotation_names_the_record(
    declaration, carrier, body
):
    """How a carrier spells the record is not how far the value travels.

    A return type written as a string, one reached through the module the
    record lives in, and a class field are the same statement about what the
    name hands back, so each makes the module importing it a holder.
    """
    sources = source_tree()
    sources["services/second_sources.py"] = declaration
    sources["services/second_writer.py"] = (
        f"from kodezart.services.second_sources import {carrier}\n\n{body}"
    )
    sites = model_value_sites(sources, identity=RECORD)
    assert "services/second_writer.py::_poll" in sites["build"]


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


#: A digest of the same shape as another text's: what a re-pin would put on a
#: record whose subject the running lane read again.
OTHER_DIGEST = "a" * 64


def later_run(digest: str = DIGEST) -> LaneBinding:
    """The next run's binding over the same lane, deliverable and base.

    Its own run id, because a run the record already carries as delivering
    another branch is the rebind the composer refuses before anything else.
    """
    return LaneBinding(
        lane_key="lane:alpha",
        body_digest=digest,
        loop_branch="ordinary-name",
        deliverable_branch="has-ralph-in-its-name",
        base_ref="trunk",
        repo_url="https://forge.example/repo",
        repo_path=None,
        run_id="run-later",
        visibility=RepoVisibility.PRIVATE,
    )


def test_a_record_with_no_digest_is_pinned_by_its_next_write():
    """Every record written before the pin existed has none.

    That is the path production takes first, so a write that carried the
    prior absence forward would leave those lanes uncompared forever and the
    criterion's comparison would never start for them.
    """
    prior = LaneRunState.model_validate({**record_data(), "bodyDigest": None})
    assert prior.body_digest is None

    later = next_lane_record(
        prior=prior,
        lane=later_run(),
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="d" * 40,
        pushed_head_sha="d" * 40,
        changeset=changeset(),
        subject="Third change",
    )

    assert later.body_digest == DIGEST


def test_the_digest_a_prior_record_pinned_is_never_re_pinned():
    """The pin belongs to the first write that had one.

    A later write carries the running lane's own digest, and preferring it
    would re-pin the record under a lane whose subject changed — the entry
    guard refuses such a lane instead, and the two are only distinguishable
    here, which is the case the rule exists for.
    """
    prior = LaneRunState.model_validate(record_data())
    assert prior.body_digest == DIGEST

    later = next_lane_record(
        prior=prior,
        lane=later_run(digest=OTHER_DIGEST),
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="d" * 40,
        pushed_head_sha="d" * 40,
        changeset=changeset(),
        subject="Third change",
    )

    assert later.body_digest == DIGEST != OTHER_DIGEST


def test_the_pull_request_the_prior_record_carries_survives_the_next_commit():
    prior = LaneRunState.model_validate(record_data())
    lane = LaneBinding(
        lane_key=prior.lane_key,
        body_digest=DIGEST,
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
        body_digest=lane.body_digest,
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


def test_a_binding_the_record_model_refuses_is_a_typed_write_refusal():
    """The model's refusal of the value it is handed is typed at the composer.

    A binding validates nothing of its own — the committing node rebuilds one
    per run from its own context — so a field whose shape the record declares
    reaches the model through this function. Callers write records through
    this module, so the refusal they see is this module's write refusal, with
    the model's own error kept as its cause.
    """
    refused = dataclasses.replace(binding(), body_digest="short")

    def compose() -> LaneRunState:
        return next_lane_record(
            prior=None,
            lane=refused,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="a" * 40,
            pushed_head_sha="a" * 40,
            changeset=changeset(commits=1, files=1),
            subject="First change",
        )

    with pytest.raises(LaneRecordWriteError) as refusal:
        compose()
    assert refusal.value.lane_key == refused.lane_key
    assert "refused field" in str(refusal.value)
    assert isinstance(refusal.value.__cause__, ValidationError)
    # Asking for the model's error catches nothing: it no longer leaves this
    # module, so the write refusal passes the inner expectation by.
    with pytest.raises(LaneRecordWriteError):
        with pytest.raises(ValidationError):
            compose()


def test_a_refused_rebind_leaves_the_prior_record_unmodified():
    """The record the refusal was raised against is the record that remains.

    Nothing about the prior record moves: not a field, not the associations it
    already carries, and not the body it renders to — so a lane whose write
    refused re-enters on exactly the facts it had.
    """
    lane = binding()
    prior = next_lane_record(
        prior=None,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="a" * 40,
        pushed_head_sha="a" * 40,
        changeset=changeset(commits=1, files=1),
        subject="First change",
    )
    snapshot = prior.model_copy(deep=True)
    body_before = lane_record_body(record=prior)
    rebound = dataclasses.replace(lane, deliverable_branch="another-deliverable")

    with pytest.raises(LaneRecordWriteError):
        next_lane_record(
            prior=prior,
            lane=rebound,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="b" * 40,
            pushed_head_sha=None,
            changeset=changeset(commits=2, files=1),
            subject="Second change",
        )

    assert prior == snapshot
    assert association_chains(prior) == association_chains(snapshot)
    assert lane_record_body(record=prior) == body_before


def test_a_repeated_write_adds_no_second_association_for_the_same_run():
    """A second commit of one run edits the record; it adds no association.

    The row-level equality a repeated head already states says nothing about
    the associations, and this write does move the head. What it must not do
    is carry the run's own deliverable and loop into the set a second time.
    """
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
    second = next_lane_record(
        prior=first,
        lane=lane,
        branch_url="https://forge.example/branch/ordinary-name",
        head_sha="b" * 40,
        pushed_head_sha="b" * 40,
        changeset=changeset(commits=2, files=1),
        subject="Second change",
    )
    assert second.head_sha != first.head_sha
    assert second.associations == first.associations
    assert role_counts(second, run_id=lane.run_id) == role_counts(
        first, run_id=lane.run_id
    )
    assert role_counts(second, run_id=lane.run_id) == {
        BranchRole.DELIVERABLE: 1,
        BranchRole.LOOP: 1,
    }


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
        body_digest=lane.body_digest,
        loop_branch=lane.loop_branch,
        deliverable_branch=lane.deliverable_branch,
        base_ref="another-base",
        repo_url=lane.repo_url,
        repo_path=lane.repo_path,
        run_id=lane.run_id,
        visibility=lane.visibility,
    )
    with pytest.raises(LaneRecordWriteError) as refusal:
        next_lane_record(
            prior=first,
            lane=rebased,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha="b" * 40,
            pushed_head_sha=None,
            changeset=changeset(commits=2, files=1),
            subject="Second change",
        )
    # Both readings, so the refusal says what the record holds as well as
    # what this commit brought: one of them alone names no disagreement.
    assert lane.base_ref in str(refusal.value)
    assert "another-base" in str(refusal.value)


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


@pytest.mark.parametrize("lane_key", ["lane:alpha", "lane:beta"])
def test_the_rows_are_the_commit_acts_and_not_the_loop_iterations(lane_key):
    """One row per commit act, whatever the loop's iteration count is (KOD-681).

    An iteration that produced no commit changed no tree, so the workspace
    still stands at the previous head and the record has nothing new to
    record for it; an iteration that landed on a head already recorded is
    likewise no new act. The trajectory is the witness here and nowhere in
    production: the record composes its rows from the commit receipt, and
    this test states what the two sequences may and may not have in common.

    Run through two lanes, because the delivered-issue column is asserted
    here: over one lane a row that carried its lane's key and a row that
    carried that key as a constant are the same answer, so the column is
    discriminating only once a second lane disagrees with the first.
    """
    lane = binding(lane_key=lane_key)
    iterations = (
        ("a" * 40, "First change"),
        (None, "Nothing to commit"),
        ("b" * 40, "Second change"),
        ("b" * 40, "Same head again"),
        (None, "Nothing to commit either"),
    )
    trajectory = fold_trajectory(
        [
            IterationRecord(
                iteration=index,
                passed_count=index,
                failing_criterion_ids=[CriterionId("KOD-681")],
                commit_sha=sha,
            )
            for index, (sha, _) in enumerate(iterations, start=1)
        ],
        plateau_window=2,
    )

    record = None
    head: str | None = None
    for sha, subject in iterations:
        head = sha if sha is not None else head
        assert head is not None
        record = next_lane_record(
            prior=record,
            lane=lane,
            branch_url="https://forge.example/branch/ordinary-name",
            head_sha=head,
            pushed_head_sha=None,
            changeset=changeset(commits=1, files=1),
            subject=subject,
        )

    assert record is not None
    assert [(row.sha, row.subject, row.issue_id) for row in record.commits] == [
        ("a" * 40, "First change", lane.lane_key),
        ("b" * 40, "Second change", lane.lane_key),
    ]
    assert len(record.commits) == 2
    assert len(trajectory.records) == 5
    assert len(record.commits) != len(trajectory.records)


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
        body_digest=lane.body_digest,
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


def declared_fields(owner: type) -> dict[str, tuple[object, ...]]:
    """Every declared field of a type the record reaches, with its annotations.

    Both readings of a model's field are kept. ``get_type_hints`` resolves a
    quoted annotation — and every annotation in a module with postponed
    evaluation — to the type it names; ``model_fields`` carries the annotation
    pydantic itself resolved, which is the concrete type where a generic model
    was parametrized and the hint is still its type variable. A field is read
    under both, so neither reading's blind spot decides what the walk sees.
    A type that is neither — a TypedDict, a NamedTuple — is read from its
    hints, which is the only place such a type declares anything.
    """
    hints = get_type_hints(owner, include_extras=True)
    if isinstance(owner, type) and issubclass(owner, BaseModel):
        return {
            name: (hints.get(name, field.annotation), field.annotation)
            for name, field in owner.model_fields.items()
        }
    return {name: (hint,) for name, hint in hints.items()}


def annotation_types(annotation: object) -> set[object]:
    """The annotation itself and every type it is composed of.

    A ``Literal`` carries values where other annotations carry types, so a
    literal ``True`` or ``False`` among its arguments is reported as ``bool``:
    a flag spelled that way is still a flag. An alias and a ``NewType`` are
    opened to what they stand for, because a name given to a flag is a flag.
    """
    if isinstance(annotation, bool):
        return {bool}
    if isinstance(annotation, TypeAliasType):
        return {annotation, *annotation_types(annotation.__value__)}
    if isinstance(annotation, NewType):
        return {annotation, *annotation_types(annotation.__supertype__)}
    arguments = get_args(annotation)
    return {annotation}.union(
        *(annotation_types(argument) for argument in arguments), set()
    )


def field_types(annotations: tuple[object, ...]) -> set[object]:
    """Every type the declared annotations of one field are composed of."""
    return set().union(*(annotation_types(item) for item in annotations), set())


def declares_fields(component: object) -> bool:
    """Whether the walk enters *component*: a class that names its own members.

    Read as "whatever declares types for its members", so a TypedDict or a
    NamedTuple carried by the record is entered on the same footing as a model
    or a dataclass: a flag declared inside one of those is still a flag.
    """
    if not isinstance(component, type):
        return False
    try:
        return bool(get_type_hints(component))
    except (NameError, TypeError):
        return False


def reached_types(roots: list[type]) -> dict[type, dict[str, tuple[object, ...]]]:
    """Every type the *roots* reach through their own declared fields.

    The surface is walked out of the annotations themselves: whatever type a
    field names, wherever it is declared, is visited and its own fields are
    read the same way, so nothing enters this guard as a named file. A type
    already walked is not walked again, so a field that names the type it is
    declared on is one visit rather than a descent with no bottom.
    """
    reached: dict[type, dict[str, tuple[object, ...]]] = {}
    pending = list(roots)
    while pending:
        owner = pending.pop()
        if owner in reached:
            continue
        reached[owner] = declared_fields(owner)
        pending.extend(
            component
            for annotations in reached[owner].values()
            for component in field_types(annotations)
            if declares_fields(component)
        )
    return reached


def boolean_fields(reached: dict[type, dict[str, tuple[object, ...]]]) -> list[str]:
    """Every declared field of the walked types whose annotation admits a bool."""
    return sorted(
        f"{owner.__name__}.{name}"
        for owner, fields in reached.items()
        for name, annotations in fields.items()
        if bool in field_types(annotations)
    )


def run_state_types() -> list[type]:
    return [
        member
        for member in vars(run_state).values()
        if isinstance(member, type) and member.__module__ == run_state.__name__
    ]


def test_no_type_the_lane_record_reaches_declares_a_boolean_field():
    """No type this record carries states a fact of its own as a flag.

    What the walk covers: the run-state types and everything their own field
    annotations name, followed wherever it is declared and however it is
    wrapped — in a union, a container, a ``Literal`` of booleans, behind an
    alias or a ``NewType`` — into models, dataclasses, TypedDicts and
    NamedTuples alike, each field read both as pydantic resolved it and as
    ``get_type_hints`` resolves it, and each type visited once.

    What it does not see, and what review has to read from the code: a field
    whose type is decided at run time; a value carried as ``object`` or
    ``Any`` and narrowed by its reader; a flag expressed as two states of a
    field this walk reads as a string; and anything a model reaches other
    than through a declared field of its own.
    """
    reached = reached_types([LaneRunState, *run_state_types()])
    assert BranchAssociation not in run_state_types()
    assert BranchAssociation in reached, "the walk stops short of the nested facts"
    assert boolean_fields(reached) == []


type PushFlag = bool
Pushed = NewType("Pushed", bool)


@pytest.mark.parametrize(
    "annotation",
    [
        bool,
        bool | None,
        tuple[bool, ...],
        Literal[True, False],
        PushFlag,
        Pushed,
    ],
)
def test_the_boolean_guard_sees_a_flag_however_it_is_wrapped(annotation):
    flag = create_model("Flag", pushed=(annotation, ...))
    assert [
        name
        for name, declared in declared_fields(flag).items()
        if bool in field_types(declared)
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


class FlagRow(TypedDict):
    pushed: bool


class FlagTuple(NamedTuple):
    pushed: bool


class Inner(BaseModel):
    pushed: bool = False


class Box[T](BaseModel):
    item: T


class SelfReferential(BaseModel):
    pushed: bool = False
    next_one: "SelfReferential | None" = None


class Deferred(BaseModel):
    """A model naming a type declared after it, so its own reading is a name."""

    carried: "DeferredFlag | None" = None


class DeferredFlag(BaseModel):
    pushed: bool = False


@pytest.mark.parametrize("owner", [PlainFlag, QuotedFlag, FlagRow, FlagTuple])
def test_the_walk_reports_a_flag_however_the_type_declaring_it_is_written(owner):
    assert boolean_fields(reached_types([owner])) == [f"{owner.__name__}.pushed"]


@pytest.mark.parametrize(
    "field",
    [
        PlainFlag,
        FlagRow,
        FlagTuple,
        Box[Inner],
        list[SelfReferential],
        Deferred,
    ],
)
def test_the_walk_reports_a_flag_nested_under_a_model(field):
    root = create_model("Nesting", carried=(field, ...))
    assert [name for name in boolean_fields(reached_types([root])) if ".pushed" in name]
