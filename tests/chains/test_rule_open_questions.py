"""The question step's place in the native fire graph, and what it opens.

Placement is the whole subject: the step stands between the re-validation
that captures the subject and the loop that executes it, it is the only way
into that loop, and a lane entered to deliver never reaches it.  The authored
graph is untouched, and no generation node runs on either path through here.
"""

import ast
import functools
import inspect
import json
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.fire_time_ruling import route_after_questions, rule_open_questions
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import (
    NoStructuredOutputError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.domain import fire_spec
from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.criteria import mint_criteria, mint_criterion_id
from kodezart.domain.criterion_cross_off import cross_offs_for
from kodezart.domain.errors import (
    FireSpecEntryError,
    SurfaceLeaseError,
    TransientAPIError,
)
from kodezart.domain.rulings import EMPTY_REGISTRY, pinned_registry
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain import criteria as criteria_types
from kodezart.types.domain import fire_spec as fire_spec_types
from kodezart.types.domain.agent import ResultEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import CriterionId, CriterionIdItem
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.fire_spec import CriterionRefItem
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.tracker import TrackerComment
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    DIRECT_OWED_TOO,
    GENERATION_NODES,
    NESTED_DONE,
    NESTED_OWED,
    OWED_KEYS,
    RECORDED_LOOP,
    SUBJECT,
    NativeExecutor,
    check_of,
    drive,
    engine,
    entry_of,
    finished,
    native_evaluation,
    native_operation,
    prepared,
    reachable,
    tracker,
)
from tests.fakes import (
    FakeChangePersister,
    FakeGitService,
    FakeTrackerPort,
    FakeWorkspaceProvider,
)
from tests.name_resolution import (
    SOURCE_ROOT,
    IdentityIndex,
    Key,
    Reference,
    carried,
    holding,
    identity_index,
    object_key,
    parsed,
    reaching,
    references,
    source_tree,
)
from tests.services.test_fire_time_rulings import (
    ARTIFACT_CHECK,
    CALL_SITE,
    CONTRADICTION_CHECK,
    EXCESS_DELIVERABLE,
    LOSING,
    MODEL,
    PRECEDENT,
    PREMISE_CHECK,
    REGROUNDED,
    STANDING,
    artifact_answer,
    artifact_body,
    contradiction_answer,
    contradiction_body,
    premise_answer,
    premise_body,
    subject_body,
)

#: Every criterion a finished subtree's roster carries, so a delivering
#: lane's review can be answered without inventing an id.
FINISHED_CHECKS = {
    key: check_of(key)
    for key in (DIRECT_OWED, DIRECT_OWED_TOO, NESTED_OWED, DIRECT_DONE, NESTED_DONE)
}

SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
STEP = "rule_open_questions"

#: The authored graph as the tree held it before the question step existed,
#: recorded off ``b871dbd2``.  Compared as literals, so a change to the
#: native arm that reached the authored one fails here rather than passing
#: a check derived from the same builder it is meant to constrain.
AUTHORED_NODES = frozenset(
    {
        "__end__",
        "__start__",
        "complete",
        "generate_branch",
        "generate_criteria",
        "generate_ticket",
        "land_best_iteration",
        "merge_to_feature",
        "persist_artifacts",
        "persist_ticket",
        "remediate",
        "resolve_visibility",
        "review_against_ticket",
        "run_ralph_loop",
        "validate_criteria",
    }
)
AUTHORED_EDGES = frozenset(
    {
        ("__start__", "generate_criteria"),
        ("__start__", "resolve_visibility"),
        ("complete", "__end__"),
        ("generate_branch", "generate_ticket"),
        ("generate_criteria", "validate_criteria"),
        ("generate_ticket", "persist_ticket"),
        ("land_best_iteration", "complete"),
        ("merge_to_feature", "complete"),
        ("merge_to_feature", "land_best_iteration"),
        ("merge_to_feature", "remediate"),
        ("merge_to_feature", "review_against_ticket"),
        ("persist_artifacts", "run_ralph_loop"),
        ("persist_ticket", "generate_criteria"),
        ("remediate", "generate_criteria"),
        ("resolve_visibility", "generate_branch"),
        ("review_against_ticket", "complete"),
        ("review_against_ticket", "remediate"),
        ("run_ralph_loop", "merge_to_feature"),
        ("validate_criteria", "complete"),
        ("validate_criteria", "generate_criteria"),
        ("validate_criteria", "persist_artifacts"),
    }
)


async def executed(fire, state, config) -> list[str]:
    """The node names this fire actually ran, in order."""
    names: list[str] = []
    assert fire.native_graph is not None
    async for update in fire.native_graph.astream(
        state, config=config, stream_mode="updates"
    ):
        names.extend(update)
    return names


async def snapshots(fire, state, config) -> list[dict]:
    """Every state this fire actually produced, in order, the last included."""
    assert fire.native_graph is not None
    return [
        snapshot
        async for snapshot in fire.native_graph.astream(
            state, config=config, stream_mode="values"
        )
    ]


def staged(executor=None, **changes):
    """A staged native fire, with the question step wired as composition does."""
    return engine(
        criteria=TrackerCriteria(tracker=changes.pop("port", None) or tracker()),
        executor=executor if executor is not None else NativeExecutor([]),
        **changes,
    )


def prepare(fire, *, entry=None):
    return fire.prepare(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=SCOPE,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key="native-fire",
        surface_holder="native-fire",
        entry=entry,
    )


async def test_a_staged_fire_runs_the_question_step_immediately_before_the_loop() -> (
    None
):
    """The executed path, not the compiled one: what this fire actually ran."""
    executor = NativeExecutor([native_evaluation(reconciled=True)])
    fire = staged(executor)
    state, config = prepare(fire)

    names = await executed(fire, state, config)

    assert STEP in names
    assert "run_ralph_loop" in names
    assert names[names.index("run_ralph_loop") - 1] == STEP
    # Execution-only: nothing that generates a subject, a criteria set or a
    # branch name ran, and no branch-name schema was asked for.
    assert not set(names) & (GENERATION_NODES | {"generate_branch"})
    assert not any("slug" in properties for properties in executor.schema_calls)
    # Non-vacuous: the pass the step opens IS one of the sessions this fire
    # ran, so the placement above is about a step that did work.
    assert len(executor.question_prompts) == 1


def test_the_native_loop_has_no_entry_but_the_question_step() -> None:
    """Deleting the step makes the loop unreachable from the entry."""
    fire = staged()
    assert fire.native_graph is not None

    assert "run_ralph_loop" in reachable(fire.native_graph, without="")
    assert "run_ralph_loop" not in reachable(fire.native_graph, without=STEP)
    # And there is exactly one source for the loop, whatever the walk finds.
    into = {
        edge.source
        for edge in fire.native_graph.get_graph().edges
        if edge.target == "run_ralph_loop"
    }
    assert into == {STEP}


def test_the_question_step_carries_the_sibling_retry_policy() -> None:
    """A transient failure of the step is retried like every other node."""
    fire = staged()
    assert fire.native_graph is not None
    nodes = fire.native_graph.nodes

    assert nodes[STEP].retry_policy == nodes["revalidate_criteria"].retry_policy
    assert nodes[STEP].retry_policy is not None


def test_the_authored_graph_is_unchanged_by_the_question_step() -> None:
    """The step is the native arm's; the authored arm is byte-for-byte as it was."""
    authored = staged().graph.get_graph()

    assert set(authored.nodes) == AUTHORED_NODES
    assert {(edge.source, edge.target) for edge in authored.edges} == AUTHORED_EDGES
    assert STEP not in AUTHORED_NODES


async def test_an_unstaged_subject_is_refused_before_any_question_session() -> None:
    """The spec read refuses first; the step never opens a pass."""
    executor = NativeExecutor([])
    fire = staged(executor, port=tracker(staged=False))

    with pytest.raises(FireSpecEntryError):
        await drive(fire, scope=SCOPE)

    assert executor.schema_calls == []
    assert executor.question_prompts == []


async def test_a_lane_entered_to_deliver_opens_no_question_session() -> None:
    """Nothing is executed, so nothing is asked, and nothing is pinned."""
    executor = NativeExecutor(
        [native_evaluation(reconciled=True, checks=FINISHED_CHECKS)]
    )
    port = tracker()
    for key in OWED_KEYS:
        finished(port, key)
    fire = staged(executor, port=port)
    state, config = prepare(fire, entry=entry_of("deliver_only"))

    names = await executed(fire, state, config)

    assert STEP not in names
    assert "run_ralph_loop" not in names
    assert "merge_to_feature" in names
    assert executor.question_prompts == []


async def test_an_engine_without_the_question_phase_refuses_before_any_session() -> (
    None
):
    """A native arm with no such phase says so, rather than reaching the loop."""
    executor = NativeExecutor([])
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker()),
        executor=executor,
        rulings=None,
    )
    state, config = prepare(fire)

    with pytest.raises(NativeWriteRefusalError, match="open-question phase"):
        await executed(fire, state, config)

    assert executor.question_prompts == []
    assert not any("criteriaResults" in p for p in executor.schema_calls)


def test_the_prepared_state_carries_nothing_the_step_produces() -> None:
    """The record is on the tracker; graph state holds no part of it."""
    state = prepared(staged(), entry=None)

    assert "ruling_unrecorded" not in state


# ---------------------------------------------------------------------------
# An answer that cannot be confirmed on the tracker ends the fire here.
# ---------------------------------------------------------------------------

RULING_PREFIX = native_operation().marker_prefixes["ruling"]

#: One answer, addressed to the Check whose own text would raise it.
ANSWER = {
    "issueRef": DIRECT_OWED,
    "question": "Is a queue holding only failed items drained?",
    "rulingClass": "pin_reading",
    "resolution": "A queue holding failed items is not drained.",
    "rejectedAlternative": "Treating failed items as drained.",
    "repoEvidence": ["policy.py — the queue predicate this tree already has"],
}

REFUTED = {
    "verdict": "refuted",
    "evidence": "The landed text names a predicate this tree does not hold.",
    "cited_refs": ["policy.py"],
}


#: The resolution a tampering board substitutes for the one that was written.
TAMPERED = "A different answer than the one written."


def is_record(comment: TrackerComment) -> bool:
    return comment.body.startswith(f"[{RULING_PREFIX}")


def altered_resolution(comment: TrackerComment, text: str) -> TrackerComment:
    """The same record comment with one field of its payload rewritten.

    The marker line and both fences are kept, so a reader still parses the
    record and mints the same identity from the same question: what it finds
    differs from what was written, rather than being unreadable.
    """
    marker, separator, rest = comment.body.partition("\n```json\n")
    payload = json.loads(rest[: -len("\n```")])
    payload["resolution"] = text
    return comment.model_copy(
        update={"body": marker + separator + json.dumps(payload, indent=2) + "\n```"}
    )


#: Each typed failure of the record's own write that leaves no confirmed
#: record, by the row that raises it.  One row per TYPE and not per message:
#: what the step converts is the type, so rows sharing one would not say
#: which of them the outcome below came from.
WRITE_FAILURES = {
    "write_unavailable": lambda: TrackerUnavailableError(
        "the record write is unavailable"
    ),
    "write_denied": lambda: TrackerAccessDeniedError(
        "the configured authority may not comment on this issue"
    ),
    "write_unreadable": lambda: TrackerProtocolError(
        "the record write answered a shape this adapter cannot read",
        tool="upsert_comment",
        detail="the response carries no comment identity",
    ),
    "write_transient": lambda: TransientAPIError("the record write answered 503"),
}


class WriteFails(FakeTrackerPort):
    """The record's own write fails; every other write of the board is fine."""

    def __init__(self, *args, failure, **kwargs):
        super().__init__(*args, **kwargs)
        self._failure = failure

    async def upsert_comment(self, *, target, marker, body, holder=None, expected=None):
        if marker.startswith(f"[{RULING_PREFIX}"):
            raise self._failure()
        return await super().upsert_comment(
            target=target, marker=marker, body=body, holder=holder, expected=expected
        )


class LeaseRefused(FakeTrackerPort):
    """The write set cannot be held, so nothing is attempted under it."""

    async def acquire_surfaces(self, *, surfaces, holder, lease_seconds):
        raise SurfaceLeaseError(
            "another run holds this write set",
            surface=next(iter(surfaces)),
            current_holder="another-job",
        )


class LeaseLost(FakeTrackerPort):
    """The grant stands and its renewal cannot confirm the declared set.

    The port answers ``None``, which names neither a competing holder nor an
    unheld surface, so the run's own lease raises out of ``renew`` rather
    than writing on under a lease it cannot vouch for.
    """

    async def renew_surfaces(self, *, surfaces, holder, lease_seconds):
        return None


class HidesRecord(FakeTrackerPort):
    """The write returns and a later reader cannot find what it wrote."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide = False

    async def list_comments(self, *, issue_key: str):
        listed = await super().list_comments(issue_key=issue_key)
        if not self.hide:
            return listed
        return tuple(comment for comment in listed if not is_record(comment))


class HidesOnJudgement(NativeExecutor):
    """Loses the record between the window's own read and the cold one.

    The window reads the artifact it wrote before it is judged, so a board
    that hides from that point on leaves the window whole and empties only
    the step's own read-back.
    """

    def __init__(self, evaluations, *, port):
        super().__init__(evaluations)
        self._port = port

    async def stream(self, **kwargs):
        properties = (kwargs.get("output_format") or {}).get("schema", {}).get(
            "properties"
        ) or {}
        if "citedRefs" in properties:
            self._port.hide = True
        async for event in super().stream(**kwargs):
            yield event


class AltersRecord(FakeTrackerPort):
    """The write returns and the text a later reader finds is not the text sent."""

    async def list_comments(self, *, issue_key: str):
        return tuple(
            altered_resolution(comment, TAMPERED) if is_record(comment) else comment
            for comment in await super().list_comments(issue_key=issue_key)
        )


class MalformsRecord(FakeTrackerPort):
    """The write returns and the framing a later reader finds is broken."""

    async def list_comments(self, *, issue_key: str):
        return tuple(
            comment.model_copy(update={"body": comment.body + "\ntampered"})
            if is_record(comment)
            else comment
            for comment in await super().list_comments(issue_key=issue_key)
        )


def variant(cls, **changes):
    """One of the boards above, over the fixture subtree the fire reads."""
    source = tracker()
    return cls(
        issues=list(source.issues.values()),
        criteria_stage_label_key=source.criteria_stage_label_key,
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members=source.scope_label_members,
        **changes,
    )


def unconfirmed(shape):
    """The board and the executor one way of failing to confirm needs."""
    if shape == "read_back_empty":
        port = variant(HidesRecord)
        executor = HidesOnJudgement([], port=port)
        executor.question_answers = [{"rulings": [ANSWER]}]
        return port, executor
    executor = NativeExecutor([])
    executor.question_answers = [{"rulings": [ANSWER]}]
    if shape in WRITE_FAILURES:
        return variant(WriteFails, failure=WRITE_FAILURES[shape]), executor
    if shape == "judged_refuted":
        executor.findings = [dict(REFUTED)]
        return variant(FakeTrackerPort), executor
    if shape == "lease_refused":
        return variant(LeaseRefused), executor
    if shape == "lease_lost":
        return variant(LeaseLost), executor
    if shape == "verifier_read_fails":
        # Hidden from the first listing on, so the window's own re-read of
        # what it just wrote is the read that cannot find it.
        port = variant(HidesRecord)
        port.hide = True
        return port, executor
    if shape == "read_back_malformed":
        return variant(MalformsRecord), executor
    return variant(AltersRecord), executor


@pytest.mark.parametrize(
    "shape",
    [
        *WRITE_FAILURES,
        "lease_refused",
        "lease_lost",
        "judged_refuted",
        "verifier_read_fails",
        "read_back_empty",
        "read_back_changed",
        "read_back_malformed",
    ],
)
async def test_an_unconfirmed_pin_halts_before_the_loop_with_its_own_outcome(
    shape,
) -> None:
    """Every way to fail, one terminal: the loop was never entered."""
    port, executor = unconfirmed(shape)
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )

    events = await drive(fire, scope=SCOPE)

    terminal = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert terminal.outcome is WorkflowOutcome.ruling_unrecorded
    assert terminal.total_iterations == 0
    # The loop is what did not happen: no execution session, no grading, and
    # no branch cut or checked out.
    assert executor.execution_prompts == []
    assert not any("criteriaResults" in props for props in executor.schema_calls)
    assert all(call.get("branch_name") is None for call in workspace.acquisitions)
    # And the question pass itself did run, so the halt is about its answer.
    assert len(executor.question_prompts) == 1


async def test_an_answer_beyond_the_stated_deliverables_halts_before_the_loop() -> None:
    """The excess arm through the composed node, not the component (KOD-629).

    The answer names work the subject's own section does not state, so it is
    raised on the issue whose text raised the question and nothing is pinned;
    the step's own update ends the fire and the loop is never entered.
    """
    port = tracker(bodies={SUBJECT: subject_body(), DIRECT_OWED: contradiction_body()})
    executor = NativeExecutor([])
    executor.question_answers = [
        {"rulings": [contradiction_answer(deliverable=EXCESS_DELIVERABLE)]}
    ]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )
    state, config = prepare(fire)
    assert fire.native_graph is not None

    updates = [
        update
        async for update in fire.native_graph.astream(
            state, config=config, stream_mode="updates"
        )
    ]

    assert next(update[STEP] for update in updates if STEP in update) == {
        "ruling_unrecorded": True
    }
    assert route_after_questions({"ruling_unrecorded": True}) == "complete"
    names = [name for update in updates for name in update]
    assert names[names.index(STEP) + 1] == "complete"
    assert "run_ralph_loop" not in names
    assert executor.execution_prompts == []
    # One question raised on the issue whose text raised it, nothing pinned.
    prefix = native_operation().marker_prefixes["escalation"]
    raised = [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}")
    ]
    assert [comment.issue_key for comment in raised] == [DIRECT_OWED]
    assert [comment for comment in port.comments if is_record(comment)] == []
    # Non-vacuous: the pass that produced the answer did open.
    assert len(executor.question_prompts) == 1


async def test_a_session_failure_is_not_the_unrecorded_answer_outcome() -> None:
    """A pass that answered nothing is the graph's failure, not the tracker's."""

    class Silent(NativeExecutor):
        async def stream(self, **kwargs):
            properties = (kwargs.get("output_format") or {}).get("schema", {}).get(
                "properties"
            ) or {}
            if "rulings" in properties:
                self.schema_calls.append(properties)
                self.question_prompts.append(kwargs["prompt"])
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="native-session",
                    structured_output=None,
                )
                return
            async for event in super().stream(**kwargs):
                yield event

    executor = Silent([])
    fire = engine(
        criteria=TrackerCriteria(tracker=variant(FakeTrackerPort)),
        executor=executor,
    )
    state, config = prepare(fire)

    with pytest.raises(NoStructuredOutputError):
        await executed(fire, state, config)

    assert len(executor.question_prompts) == 1


async def test_a_remediation_round_passes_the_step_again_and_writes_nothing() -> None:
    """The round re-enters through the step, and the board already has the answer."""
    executor = NativeExecutor(
        [
            native_evaluation(failed=True),
            *[native_evaluation(reconciled=True) for _ in range(4)],
        ]
    )
    executor.question_answers = [
        {"rulings": [ANSWER]},
        {"rulings": [ANSWER]},
    ]
    port = variant(FakeTrackerPort)
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        remediation_rounds=1,
        git=git,
        workspace=workspace,
    )

    await drive(fire, scope=SCOPE)

    # Two passes through the step, one record, and the second pass was shown
    # the first one's text.
    assert len(executor.question_prompts) == 2
    records = [c for c in port.comments if is_record(c)]
    assert len(records) == 1
    assert ANSWER["resolution"] in executor.question_prompts[1]
    # And exactly one write of that record happened, on the first pass.
    assert [
        write
        for write in port.comment_writes
        if write[1].startswith(f"[{RULING_PREFIX}") or ANSWER["resolution"] in write[1]
    ] == [(records[0].comment_key, records[0].body)]
    # An identical re-write of identical text is invisible in that journal, so
    # the two things a second write cannot do without: the record was judged
    # once, and its surface was held once — one grant and the one renewal the
    # write makes inside it.
    assert len(executor.judge_sessions) == 1
    held = [
        lease
        for lease in port.lease_writes
        if any(
            surface.marker.startswith(f"[{RULING_PREFIX}") for surface in lease.surfaces
        )
    ]
    assert len(held) == 2


# ---------------------------------------------------------------------------
# The loop consumes what the step pinned, read off the tracker.
# ---------------------------------------------------------------------------


class SnapshottingExecutor(NativeExecutor):
    """Records what the board carried at the moment each writer session opened."""

    def __init__(self, evaluations, *, port):
        super().__init__(evaluations)
        self._port = port
        self.board_at_execution: list[tuple[str, ...]] = []

    async def stream(self, **kwargs):
        properties = (kwargs.get("output_format") or {}).get("schema", {}).get(
            "properties"
        ) or {}
        if "claims" in properties:
            self.board_at_execution.append(
                tuple(c.body for c in self._port.comments if is_record(c))
            )
        async for event in super().stream(**kwargs):
            yield event


def registry_block(prompt: str) -> str:
    """The pinned-answers block of a writer prompt, and nothing around it."""
    return prompt.partition("<pinned_rulings>")[2].partition("</pinned_rulings>")[0]


class AltersAfterStep(FakeTrackerPort):
    """Rewrites the answer a reader finds, once it is told the step is done.

    The step's own read-back has already run by then, so whatever the loop
    renders can only be what a reader finds on the board now.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.alter = False

    async def list_comments(self, *, issue_key: str):
        listed = await super().list_comments(issue_key=issue_key)
        if not self.alter:
            return listed
        return tuple(
            altered_resolution(comment, TAMPERED) if is_record(comment) else comment
            for comment in listed
        )


@pytest.mark.parametrize("answered", [True, False])
async def test_the_first_iteration_prompt_carries_the_pinned_answer(
    answered,
) -> None:
    """The record the step wrote is what the first iteration is shown."""
    port = variant(FakeTrackerPort)
    executor = SnapshottingExecutor(
        [native_evaluation(reconciled=True) for _ in range(4)], port=port
    )
    executor.question_answers = [{"rulings": [ANSWER] if answered else []}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )
    state, config = prepare(fire)

    produced = await snapshots(fire, state, config)

    prompt = executor.execution_prompts[0]
    block = registry_block(prompt)
    if not answered:
        assert block.strip() == EMPTY_REGISTRY
        assert ANSWER["question"] not in prompt
        return
    records = await RulingRecordReader(
        tracker=port, operation=native_operation()
    ).read_issue(issue_key=DIRECT_OWED)
    assert len(records) == 1
    record = records[0][1]
    assert pinned_registry((record,)) in block
    assert ANSWER["resolution"] in block
    # The question stands ONLY inside that block, in the record that answers it.
    assert ANSWER["question"] in block
    assert ANSWER["question"] not in prompt.replace(block, "")
    # The record was on the board when the writer session opened, so the text
    # above was read back from the tracker rather than carried in run state.
    assert executor.board_at_execution and all(
        len(snapshot) == 1 for snapshot in executor.board_at_execution
    )
    # And no channel of any state this graph produced carries it, the last
    # state the walk left included.
    assert produced
    for snapshot in produced:
        assert all(
            ANSWER["resolution"] not in str(value) for value in snapshot.values()
        )


@pytest.mark.parametrize(
    "body, answer, check, pinned",
    [
        pytest.param(
            contradiction_body,
            contradiction_answer,
            CONTRADICTION_CHECK,
            (STANDING, LOSING),
            id="resolve_contradiction",
        ),
        pytest.param(
            artifact_body,
            artifact_answer,
            ARTIFACT_CHECK,
            (MODEL, CALL_SITE, PRECEDENT),
            id="pin_artifact",
        ),
        pytest.param(
            premise_body,
            premise_answer,
            PREMISE_CHECK,
            (REGROUNDED,),
            id="reground_premise",
        ),
    ],
)
async def test_the_first_iteration_prompt_carries_what_each_class_of_answer_pins(
    body, answer, check, pinned
) -> None:
    """Whatever a class pins reaches the loop the same way: off the board, in the block.

    The reading class is the test above; these are the other classes, one row
    each.  What is asserted per row is the text that class exists to pin, and
    that the question it answers stands only inside the block — while the
    Check that raised it is still in front of the writer, so the block is not
    carrying the whole prompt.
    """
    port = tracker(bodies={DIRECT_OWED: body()})
    answered = answer()
    executor = SnapshottingExecutor(
        [native_evaluation(reconciled=True) for _ in range(4)], port=port
    )
    executor.question_answers = [{"rulings": [answered]}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )
    state, config = prepare(fire)

    produced = await snapshots(fire, state, config)

    prompt = executor.execution_prompts[0]
    block = registry_block(prompt)
    outside = prompt.replace(block, "")
    ((_, record),) = await RulingRecordReader(
        tracker=port, operation=native_operation()
    ).read_issue(issue_key=DIRECT_OWED)
    assert pinned_registry((record,)) in block
    for text in pinned:
        assert text in block
    # The question stands only inside the block, in the record that answers it,
    # and the Check whose text raised it still reaches the writer.
    assert answered["question"] in block
    assert answered["question"] not in outside
    assert check in outside
    # What an artifact answer pins is nowhere in the prompt but the block: the
    # model, the call site and the precedent reach iteration 1 through the
    # record alone.  The contradiction's two sides are the Check's own words
    # and stand outside it legitimately, so that row is not held to this.
    if body is artifact_body:
        assert all(text not in outside for text in pinned)
    # Read off the board at loop start, not carried in graph state.  One
    # writer session opened, and it saw exactly the one record.
    assert len(executor.execution_prompts) == 1
    assert [len(snapshot) for snapshot in executor.board_at_execution] == [1]
    assert produced and all(
        answered["resolution"] not in str(value)
        for snapshot in produced
        for value in snapshot.values()
    )


async def test_the_first_iterations_block_is_read_off_the_tracker_at_loop_start() -> (
    None
):
    """The record changes on the board after the step, and the loop shows that.

    Nothing the step returned can account for the text below: the answer it
    wrote is not the answer a reader finds when the loop opens.
    """
    port = variant(AltersAfterStep)
    executor = NativeExecutor([native_evaluation(reconciled=True) for _ in range(4)])
    executor.question_answers = [{"rulings": [ANSWER]}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )
    state, config = prepare(fire)
    assert fire.native_graph is not None

    # The board is rewritten between two supersteps: after the step returned,
    # before the loop node runs.
    async for update in fire.native_graph.astream(
        state, config=config, stream_mode="updates"
    ):
        if STEP in update:
            port.alter = True

    block = registry_block(executor.execution_prompts[0])
    assert TAMPERED in block
    assert ANSWER["resolution"] not in block


async def test_the_step_returns_no_update_into_graph_state() -> None:
    """Called directly, with the record written: nothing to carry forward."""
    port = variant(FakeTrackerPort)
    executor = NativeExecutor([])
    executor.question_answers = [{"rulings": [ANSWER]}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        git=git,
        workspace=workspace,
    )
    source = TrackerCriteria(tracker=port)
    spec, current = await source.read_entry(issue_key=SUBJECT)
    _, config = prepare(fire)

    update = await rule_open_questions(
        {
            "fire_spec": spec,
            "criterion_set": current,
            "work_base_ref": "main",
            "repo_visibility": RepoVisibility.PUBLIC,
        },
        config,
        rulings=fire.rulings,
    )

    assert update == {}
    # Non-vacuous: the pass did answer, and the answer is on the tracker.
    assert [comment for comment in port.comments if is_record(comment)]


async def test_the_question_step_mints_a_pinned_answer_identity_and_no_criterion_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step addresses questions, never criteria (KOD-639).

    The subject and its roster are captured first, through the reader that
    legitimately mints criterion identities.  Only then is
    ``fire_spec.criterion_ref`` replaced by a sentinel, so anything the step
    itself reaches there raises.  ``AssertionError`` is deliberate: it is not
    one of the failures the step converts into an unrecorded-answer outcome,
    so a reached mint cannot be swallowed into a returned state key.
    """
    port = variant(FakeTrackerPort)
    executor = NativeExecutor([])
    executor.question_answers = [{"rulings": [ANSWER]}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        git=git,
        workspace=workspace,
    )
    source = TrackerCriteria(tracker=port)
    spec, current = await source.read_entry(issue_key=SUBJECT)
    _, config = prepare(fire)

    def _reached(key: str) -> None:
        raise AssertionError(f"a criterion identity was minted for {key!r}")

    monkeypatch.setattr(fire_spec, "criterion_ref", _reached)

    update = await rule_open_questions(
        {
            "fire_spec": spec,
            "criterion_set": current,
            "work_base_ref": "main",
            "repo_visibility": RepoVisibility.PUBLIC,
        },
        config,
        rulings=fire.rulings,
    )

    assert update == {}
    # Non-vacuous: the pass answered, the answer is on the tracker, and the
    # identity it is addressed by is the one the question mints.
    assert [comment for comment in port.comments if is_record(comment)]
    ((_, record),) = await RulingRecordReader(
        tracker=port, operation=native_operation()
    ).read_issue(issue_key=DIRECT_OWED)
    assert record.ruling_id == mint_ruling_id(
        issue_ref=DIRECT_OWED, question=str(ANSWER["question"])
    )
    # The sentinel is live, not inert: the reader that does mint one still
    # reaches it under exactly the same patch.
    with pytest.raises(AssertionError, match="criterion identity"):
        await source.read_entry(issue_key=SUBJECT)


#: The mint the question path must never reach, by the shipped function's own
#: word, so a rename moves the census below with the code.
CRITERION_MINT = fire_spec.criterion_ref.__name__

#: The criterion identities themselves, each read off the shipped type: the
#: native identity the mint returns and the authored one. Both are
#: ``NewType``s constructed bare wherever a key is captured, so constructing
#: one is minting one. Every other definition that mints is derived from
#: these two over the tree rather than listed here (KOD-639).
IDENTITY_TYPES = frozenset({object_key(CriterionRef), object_key(CriterionId)})

#: Every module of the pre-loop question path: the node, the component it
#: drives, the arithmetic that component does, and the reader of what it wrote.
RULING_PATH = (
    "chains/fire_time_ruling.py",
    "services/fire_time_rulings.py",
    "domain/rulings.py",
    "services/ruling_records.py",
)

#: The modules that may name the mint at all, other than the one defining it,
#: against what each names it for. Crossing a criterion off addresses that
#: criterion by its identity, which is the one legitimate reason to mint one
#: outside the module that owns the mint.
MINT_CALLERS = {"domain/criterion_cross_off.py"}


@functools.cache
def _shipped_sources() -> Mapping[str, str]:
    """Every shipped module's text by its tree-relative path, read once."""
    return MappingProxyType(source_tree())


@functools.cache
def _shipped_trees() -> Mapping[str, ast.Module]:
    """The same modules parsed once; no walk below changes a tree."""
    return MappingProxyType(parsed(_shipped_sources()))


def _source_trees() -> dict[str, ast.Module]:
    """Every shipped module by its tree-relative path, parsed once."""
    return dict(_shipped_trees())


def _planted(edits: Mapping[str, str]) -> dict[str, ast.Module]:
    """The shipped tree with each module of *edits* extended by its text.

    A module the tree does not have is created with that text alone.
    """
    trees = _source_trees()
    for module, text in edits.items():
        trees[module] = ast.parse(_shipped_sources().get(module, "") + "\n" + text)
    return trees


def _bound_key(module: ModuleType, value: object) -> Key:
    """The definition a module-level value is, by the one word binding it."""
    (word,) = [name for name, bound in vars(module).items() if bound is value]
    home = Path(module.__file__ or "").resolve().relative_to(SOURCE_ROOT.resolve())
    return home.as_posix(), word


def _minting(index: IdentityIndex, found: Sequence[Reference]) -> frozenset[Key]:
    """Every definition of the tree that brings a criterion identity into being.

    The two identity types, and every definition whose own text names one of
    them — or names a definition that does — outside an annotation, or whose
    declared return names one, to a fixed point. So the native mint, the
    authored mint, a function that mints for every row it captures, a ``def``
    wrapping any of them, a type alias over an identity, and a module-level
    alias of any of these, however it is written, are each found without
    being named here.
    """
    return reaching(index, IDENTITY_TYPES, found=found)


def question_path_mints(trees: Mapping[str, ast.Module]) -> list[str]:
    """Every way a module of the question path reaches a criterion mint.

    Two readings over one index of the tree, each resolved by identity. A
    path module NAMES a minting definition — as a callee, a value, an import
    or an annotation, under any word an import, alias or re-export gives it.
    Or a path module HOLDS one handed in from elsewhere: a parameter, a name,
    a declared field or an instance attribute of the path that a minting
    value reaches by a positional or keyword argument, a parameter default,
    an assignment, a ``for`` target, a walrus or the return of a function it
    passes through, carried by a conditional, a boolean fallback, a tuple or
    dict display, a subscript, ``await``, a lambda or nested ``def``,
    ``functools.partial`` or ``cast``, through any number of calls. Each of
    those is a row of the control below.
    """
    index = identity_index(trees)
    found = references(index)
    mints = _minting(index, found)
    named = {
        f"{reference.module}:{reference.line} names {reference.key[1]}"
        for reference in found
        if reference.module in RULING_PATH and reference.key in mints
    }
    held = {
        f"{scope[0]} {scope[1]} holds {word}"
        for scope, word in holding(index, carried(index, mints), RULING_PATH)
    }
    return sorted(named | held)


def _names_the_mint(tree: ast.Module, wanted: frozenset[str]) -> bool:
    """Whether this module names any of *wanted* as a value, import or definition.

    The name as an identifier, never as text: ``types/domain/criterion_ref.py``
    is a module path and ``CRITERION_REFUTED`` is a different name, so a
    substring search over the tree would report both and say nothing.
    """
    return any(
        (isinstance(node, ast.Name) and node.id in wanted)
        # The module-attribute form, which is the one the sentinel can see.
        or (isinstance(node, ast.Attribute) and node.attr in wanted)
        # The from-import form, which is the one it cannot.
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name in wanted for alias in node.names)
        )
        or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in wanted
        )
        for node in ast.walk(tree)
    )


def _naming(trees: dict[str, ast.Module], wanted: frozenset[str]) -> set[str]:
    """Every module of the shipped tree that names any of *wanted*."""
    return {path for path, tree in trees.items() if _names_the_mint(tree, wanted)}


def test_no_module_on_the_ruling_path_names_the_criterion_mint() -> None:
    """The negative clause, by identity rather than by word (KOD-639).

    The behavioural guard above replaces the mint in the module dict, so it
    sees a call reached as ``fire_spec.criterion_ref(...)`` and nothing of a
    caller that did ``from kodezart.domain.fire_spec import criterion_ref`` —
    which binds the function at import time, before any patch, and is the form
    this tree already uses elsewhere. ``CriterionRef`` is a ``NewType`` over
    ``str``, so a mint added on this path changes no value a test could read:
    the naming site is the only observable thing there is.

    So it is read statically, over the whole shipped tree, and keyed on the
    definitions themselves rather than on their words. What counts as a mint
    is derived: the two identity types, then every definition that names one
    outside an annotation or declares one as its return, to a fixed point —
    the native mint, the authored mint, ``tracker_spec_from_issues``, the
    ``Annotated`` aliases over either type, the classes whose methods mint,
    and anything that wraps, re-binds or re-exports one of them. A path
    module then may neither name any of them nor hold one handed in; see
    ``question_path_mints`` for what each reading follows. A mint written onto
    the path in each of those ways is a case of the control below.

    The census stays on the mint function's word alone, since the identity
    types are legitimately named across ``types/``, ``chains/criteria.py``
    and ``domain/criteria.py`` and a total census over them would say
    nothing.

    What this does not see. A method called through an instance whose class
    the calling module does not name — ``self._criteria.read_entry()`` on an
    attribute typed by a protocol — because the receiver's class is not
    resolved; a class whose method mints is itself a mint, so a path module
    constructing or annotating it is reported, but one that holds it under a
    protocol's name is not. A value put into a container by a method call
    rather than written in its display, or wrapped by a call other than
    ``functools.partial`` and ``cast``. A definition fetched by ``getattr``,
    ``importlib`` or ``__dict__`` under a name built at run time, and
    ``eval``/``exec``.
    """
    trees = _source_trees()
    index = identity_index(trees)
    mints = _minting(index, references(index))

    # Non-vacuous, and derived rather than listed: the set is more than the
    # two types it grows from, and it holds each shipped mint, read off the
    # objects, without having been told any of them.
    assert mints > IDENTITY_TYPES
    assert {
        object_key(fire_spec.criterion_ref),
        object_key(fire_spec.tracker_spec_from_issues),
        object_key(mint_criterion_id),
        object_key(mint_criteria),
        _bound_key(criteria_types, CriterionIdItem),
        _bound_key(fire_spec_types, CriterionRefItem),
    } <= mints
    # And the handing-on walk the clause's second reading rests on finds the
    # holders the shipped tree has, off the path, so an empty walk reddens here.
    assert carried(index, mints).holders

    # The module that defines the mint is derived rather than named.
    (definer,) = {
        path
        for path, tree in trees.items()
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == CRITERION_MINT
            for node in ast.walk(tree)
        )
    }
    assert definer == object_key(fire_spec.criterion_ref)[0] == "domain/fire_spec.py"

    # The clause: no module of the question path names a mint or holds one.
    assert question_path_mints(trees) == []
    # And the census over the mint function is total, so a naming site
    # anywhere else is reported too.
    assert _naming(trees, frozenset({CRITERION_MINT})) == {definer} | MINT_CALLERS


def _held_attribute() -> str:
    """The first attribute the question service holds on itself, off its code."""
    init = ast.parse(textwrap.dedent(inspect.getsource(FireTimeRulings.__init__)))
    return next(
        target.attr
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


#: The modules a planted mint lands in, each read off a definition it holds:
#: the question service and its arithmetic on the path, the one module
#: licensed to name the mint, and a module the tree does not have.
_SERVICE = object_key(FireTimeRulings)[0]
_ARITHMETIC = object_key(pinned_registry)[0]
_LICENSED = object_key(cross_offs_for)[0]
_ELSEWHERE = "services/second_wiring.py"
_ELSEWHERE_MODULE = f"{SOURCE_ROOT.name}.services.second_wiring"
#: The words a plant spells, each the shipped object's own.
_MINT = f"from {fire_spec.__name__} import {CRITERION_MINT}\n"
_REF = f"from {CriterionRef.__module__} import {CriterionRef.__name__}\n"
_AUTHORED = f"from {mint_criterion_id.__module__} import {mint_criterion_id.__name__}\n"
_ITEM = _bound_key(criteria_types, CriterionIdItem)[1]
_PACKAGE, _MODULE = fire_spec.__name__.rsplit(".", 1)
#: A path function that calls a value handed to it, and a path class that
#: holds one on itself and calls it — the two ways in for a handed mint.
_TAKES = (
    "def _address(key, mint):\n"
    "    return mint(key)\n"
    "class _Addresser:\n"
    "    def __init__(self, mint):\n"
    "        self._mint = mint\n"
    "    def use(self, mint):\n"
    "        return mint('k')\n"
)
_TAKEN = f"from {pinned_registry.__module__} import _address, _Addresser\n"
#: A path function calling a word re-exported from the licensed module.
_CALLS_ALIAS = (
    f"from {cross_offs_for.__module__} import address_one\n"
    "def _minted(key):\n"
    "    return address_one(key)\n"
)

#: Each way a criterion mint could reach the question path: the modules it
#: arrives in and the text each is extended by. Every row is an ordinary
#: spelling, not reflection over a built name, and every one must be
#: reported (KOD-639).
PLANTED_PATH_MINTS: dict[str, dict[str, str]] = {
    "the native identity constructed bare on the path": {
        _ARITHMETIC: f"{_REF}def _addressed(key):\n"
        f"    return {CriterionRef.__name__}(key)\n",
    },
    "the authored mint called on the path": {
        _ARITHMETIC: f"{_AUTHORED}def _authored(index):\n"
        f"    return {mint_criterion_id.__name__}(index)\n",
    },
    "a type alias over an identity validated on the path": {
        _ARITHMETIC: "from pydantic import TypeAdapter\n"
        f"from {criteria_types.__name__} import {_ITEM}\n"
        "def _validated(key):\n"
        f"    return TypeAdapter({_ITEM}).validate_python(key)\n",
    },
    "a shipped function that mints each row, called on the path": {
        _SERVICE: f"from {_PACKAGE} import {_MODULE} as _spec\n"
        "def _captured(subject, rows):\n"
        f"    return _spec.{fire_spec.tracker_spec_from_issues.__name__}("
        "subject=subject, criteria=rows)\n",
    },
    "a re-export of the mint under a second word": {
        _LICENSED: f"address_one = {CRITERION_MINT}\n",
        _SERVICE: _CALLS_ALIAS,
    },
    "a def wrapping the mint in the licensed module": {
        _LICENSED: f"def address_one(key):\n    return {CRITERION_MINT}(key)\n",
        _SERVICE: _CALLS_ALIAS,
    },
    "a partial of the mint in the licensed module": {
        _LICENSED: "from functools import partial\n"
        f"address_one = partial({CRITERION_MINT})\n",
        _SERVICE: _CALLS_ALIAS,
    },
    "a tuple-unpacking alias of the mint": {
        _LICENSED: f"(address_one,) = ({CRITERION_MINT},)\n",
        _SERVICE: _CALLS_ALIAS,
    },
    "a conditional alias of the mint": {
        _LICENSED: f"address_one = {CRITERION_MINT} if {CRITERION_MINT} else None\n",
        _SERVICE: _CALLS_ALIAS,
    },
    "a re-export chain two modules long": {
        _LICENSED: f"address_one = {CRITERION_MINT}\n",
        _ELSEWHERE: f"from {cross_offs_for.__module__} import "
        "address_one as address_two\n",
        _SERVICE: f"from {_ELSEWHERE_MODULE} import address_two\n"
        "def _minted(key):\n"
        "    return address_two(key)\n",
    },
    "a mint handed back by a licensed function": {
        _LICENSED: f"def address_one():\n    return {CRITERION_MINT}\n",
        _SERVICE: f"from {cross_offs_for.__module__} import address_one\n"
        "def _minted(key):\n"
        "    return address_one()(key)\n",
    },
    "the mint read as an attribute of a module alias": {
        _SERVICE: f"import {fire_spec.__name__} as _spec\n"
        "def _minted(key):\n"
        f"    return _spec.{CRITERION_MINT}(key)\n",
    },
    "the mint fetched by getattr with a literal word": {
        _SERVICE: f"import {fire_spec.__name__} as _spec\n"
        "def _minted(key):\n"
        f"    return getattr(_spec, {CRITERION_MINT!r})(key)\n",
    },
    "the mint imported inside a function": {
        _SERVICE: "def _minted(key):\n"
        f"    from {fire_spec.__name__} import {CRITERION_MINT} as mint\n"
        "    return mint(key)\n",
    },
    "the mint imported relatively": {
        _SERVICE: f"from ..{_PACKAGE.split('.', 1)[1]}.{_MODULE} "
        f"import {CRITERION_MINT}\n"
        "def _minted(key):\n"
        f"    return {CRITERION_MINT}(key)\n",
    },
    "the native identity reached through a star import": {
        _ARITHMETIC: f"from {CriterionRef.__module__} import *\n"
        "def _addressed(key):\n"
        f"    return {CriterionRef.__name__}(key)\n",
    },
    "the native identity handed to a path function": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, {CriterionRef.__name__})\n",
    },
    "the native identity handed to a path constructor": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire():\n"
        f"    return _Addresser({CriterionRef.__name__})\n",
    },
    "the native identity handed on through a second function": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def relay(key, mint):\n"
        "    return _address(key, mint)\n"
        "def wire(key):\n"
        f"    return relay(key, {CriterionRef.__name__})\n",
    },
    "the native identity imported inside a function and handed to the path": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}def wire(key):\n"
        f"    from {CriterionRef.__module__} import {CriterionRef.__name__} as ref\n"
        "    return _address(key, ref)\n",
    },
    "a lambda over the mint handed to a path function": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_MINT}def wire(key):\n"
        f"    return _address(key, lambda value: {CRITERION_MINT}(value))\n",
    },
    "the native identity handed to a path method through an instance": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_REF}def wire(addresser):\n"
        f"    return addresser.use({CriterionRef.__name__})\n",
    },
    "the native identity assigned onto an attribute the path holds": {
        _ELSEWHERE: f"{_REF}def rewire(service):\n"
        f"    service.{_held_attribute()} = {CriterionRef.__name__}\n",
    },
    "the native identity bound on self by a base class another module holds": {
        _ELSEWHERE: f"{_REF}class Base:\n"
        "    def __init__(self, mint):\n"
        "        self._mint = mint\n"
        "def wire():\n"
        f"    return Base({CriterionRef.__name__})\n",
        _ARITHMETIC: f"from {_ELSEWHERE_MODULE} import Base\n"
        "class _Addressing(Base):\n"
        "    def use(self, key):\n"
        "        return self._mint(key)\n",
    },
    # The shapes a handed value is carried through on its way in, one row
    # each, so undoing any one reading of the walk reddens its row.
    "a nested def over the mint handed to a path function": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_MINT}def wire(key):\n"
        "    def mint(value):\n"
        f"        return {CRITERION_MINT}(value)\n"
        "    return _address(key, mint)\n",
    },
    "the native identity handed by keyword": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, mint={CriterionRef.__name__})\n",
    },
    "the native identity handed through a conditional": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, {CriterionRef.__name__} if key else None)\n",
    },
    "the native identity handed as a fallback": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key, override):\n"
        f"    return _address(key, override or {CriterionRef.__name__})\n",
    },
    "the native identity handed out of a tuple display": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, ({CriterionRef.__name__},)[0])\n",
    },
    "the native identity handed out of a dict display": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, {{'mint': {CriterionRef.__name__}}}['mint'])\n",
    },
    "the native identity handed through a walrus": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    return _address(key, (mint := {CriterionRef.__name__}))\n",
    },
    "the native identity bound by an annotated assignment and handed on": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    mint: object = {CriterionRef.__name__}\n"
        "    return _address(key, mint)\n",
    },
    "the native identity handed as a partial": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}from functools import partial\n"
        "def wire(key):\n"
        f"    return _address(key, partial({CriterionRef.__name__}))\n",
    },
    "the native identity handed through a cast": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}from typing import Any, cast\n"
        "def wire(key):\n"
        f"    return _address(key, cast(Any, {CriterionRef.__name__}))\n",
    },
    "the native identity returned by a helper and awaited on the way in": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}async def _ready(value):\n"
        "    return value\n"
        "async def wire(key):\n"
        f"    return _address(key, await _ready({CriterionRef.__name__}))\n",
    },
    "the native identity as a parameter default handed on": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key, mint={CriterionRef.__name__}):\n"
        "    return _address(key, mint)\n",
    },
    "the native identity bound by a for loop and handed on": {
        _ARITHMETIC: _TAKES,
        _ELSEWHERE: f"{_TAKEN}{_REF}def wire(key):\n"
        f"    for mint in ({CriterionRef.__name__},):\n"
        "        return _address(key, mint)\n",
    },
    "the native identity handed to a declared field of a path class": {
        _ARITHMETIC: "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class _Held:\n"
        "    mint: object\n",
        _ELSEWHERE: f"from {pinned_registry.__module__} import _Held\n"
        f"{_REF}def wire():\n"
        f"    return _Held({CriterionRef.__name__})\n",
    },
}


@pytest.mark.parametrize("form", sorted(PLANTED_PATH_MINTS))
def test_each_way_a_mint_reaches_the_question_path_is_reported(form: str) -> None:
    """Every row reddens the clause, and each is a control on one widening.

    Undoing the fixed point over bodies, the alias forms, the import forms or
    the handing-on walk each leaves one of these rows unreported. The
    shipped tree reporting nothing is asserted once, in the case above.
    """
    assert question_path_mints(_planted(PLANTED_PATH_MINTS[form]))


# ---------------------------------------------------------------------------
# The step writes zero code: read-only sessions, a detached tree, no commit.
# ---------------------------------------------------------------------------

#: Every Git verb that could move a head, by the name the service uses.
MOVING = ("commit", "push", "add_all", "reset_hard")


class StepWatchingExecutor(NativeExecutor):
    """Records how many trees were open when each of the step's passes ran.

    The graph goes on past the step, and later nodes open trees of their own,
    so "the trees this step opened" has to be read at the step rather than off
    the whole run's list.
    """

    def __init__(self, evaluations, *, workspace):
        super().__init__(evaluations)
        self._workspace = workspace
        self.trees_at_step: list[int] = []

    async def stream(self, **kwargs):
        properties = (kwargs.get("output_format") or {}).get("schema", {}).get(
            "properties"
        ) or {}
        if "rulings" in properties or "citedRefs" in properties:
            self.trees_at_step.append(len(self._workspace.acquisitions))
        async for event in super().stream(**kwargs):
            yield event


@pytest.mark.parametrize("kind", ["new", "resumed"])
async def test_the_question_session_is_read_only_and_moves_no_branch(kind) -> None:
    """The pass and its judge both run under the evaluation configuration."""
    port = variant(FakeTrackerPort)
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    executor = StepWatchingExecutor(
        [native_evaluation(reconciled=True) for _ in range(4)], workspace=workspace
    )
    executor.question_answers = [{"rulings": [ANSWER]}]
    persister = FakeChangePersister()
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        git=git,
        workspace=workspace,
        persister=persister,
    )
    state, config = prepare(fire, entry=entry_of(kind))

    await executed(fire, state, config)

    for call in (*executor.question_sessions, *executor.judge_sessions):
        assert call["permission_mode"] is EVAL_PERMISSION_MODE
        assert call["allowed_tools"] is ToolPreset.EVALUATION
        assert call["agents"] == NO_SUBAGENTS
    assert executor.question_sessions and executor.judge_sessions
    # Every tree the step opened is detached and names no branch.
    opened = workspace.acquisitions[: max(executor.trees_at_step)]
    assert len(opened) == 2
    for call in opened:
        assert call["create_branch"] is False
        assert call["branch_name"] is None
    # The step's own tree stands at the ref the run entered on: the base for a
    # new lane, the recorded branch for one continuing its own work. The
    # judge's stands at the exact commit that tree reported.
    assert opened[0]["ref"] == state["work_base_ref"]
    assert opened[1]["ref"] == "a" * 40
    if kind == "resumed":
        # The literal, so a change in how the entry derives that ref fails here.
        assert opened[0]["ref"] == RECORDED_LOOP
    # Each of those trees was given back before the next was taken.
    assert [name for name, *_ in workspace.calls[:4]] == [
        "acquire",
        "release",
        "acquire",
        "release",
    ]
    # Nothing was committed, pushed or reset, and nothing was persisted.
    assert not [name for name, *_ in git.calls if name in MOVING]
    assert persister.calls == []


async def test_the_judge_is_built_from_one_repository_source() -> None:
    """Both a path and a URL are in hand, and the judge takes exactly one."""
    port = variant(FakeTrackerPort)
    executor = NativeExecutor([native_evaluation(reconciled=True)])
    executor.question_answers = [{"rulings": [ANSWER]}]
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        git=git,
        workspace=workspace,
    )

    # ``drive`` hands the fire both a repo_path and a repo_url.
    await drive(fire, scope=SCOPE)

    assert executor.judge_sessions
    # Every tree of this path is cut from the path; the judge's own, which is
    # the one built with exactly one source, took the path and not the URL.
    assert workspace.acquisitions
    assert all(call["repo_path"] == "/tmp/fire" for call in workspace.acquisitions)
    judged = [call for call in workspace.acquisitions if call["repo_url"] is None]
    assert judged == [
        call for call in workspace.acquisitions if call["ref"] == "a" * 40
    ]
    assert [c for c in port.comments if is_record(c)]
