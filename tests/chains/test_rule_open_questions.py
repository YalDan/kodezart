"""The question step's place in the native fire graph, and what it opens.

Placement is the whole subject: the step stands between the re-validation
that captures the subject and the loop that executes it, it is the only way
into that loop, and a lane entered to deliver never reaches it.  The authored
graph is untouched, and no generation node runs on either path through here.
"""

import ast
import json
from pathlib import Path

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
from kodezart.domain.errors import (
    FireSpecEntryError,
    SurfaceLeaseError,
    TransientAPIError,
)
from kodezart.domain.rulings import EMPTY_REGISTRY, pinned_registry
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import ResultEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import CriterionId
from kodezart.types.domain.criterion_ref import CriterionRef
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


#: The mint the ruling path must never reach, and the tree it is scanned over.
#: The word is the shipped function's own, so a rename moves the guard.
CRITERION_MINT = fire_spec.criterion_ref.__name__
SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"

#: Every spelling a criterion identity is brought into being under, each read
#: off the shipped object: the mint function, the native identity that mint
#: itself returns, and the authored identity. Both identity types are
#: ``NewType``s constructed bare wherever a key is captured, so constructing
#: one is minting one, and the native one is what the question path would
#: reach for. Derived from the objects rather than listed here, so neither a
#: rename nor a hand-typed twin can leave a spelling out (KOD-639).
CRITERION_IDENTITIES = frozenset(
    {CRITERION_MINT, CriterionRef.__name__, CriterionId.__name__}
)

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


def _source_trees() -> dict[str, ast.Module]:
    """Every shipped module by its tree-relative path, parsed once."""
    return {
        str(module.relative_to(SOURCE_ROOT)): ast.parse(
            module.read_text(encoding="utf-8")
        )
        for module in SOURCE_ROOT.rglob("*.py")
    }


def _bound_to(tree: ast.Module, known: frozenset[str]) -> set[str]:
    """Every name this module binds to one of *known* itself.

    The value rather than what it returns: ``address_one = criterion_ref``
    hands the mint out under a second word, while ``key = criterion_ref(row)``
    hands out the identity it minted, which is an ordinary value. An aliased
    import is the same re-binding written as an import.
    """

    def resolves(value: ast.expr) -> bool:
        return (isinstance(value, ast.Name) and value.id in known) or (
            isinstance(value, ast.Attribute) and value.attr in known
        )

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and resolves(node.value):
            bound |= {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
            and resolves(node.value)
        ):
            bound.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            bound |= {
                alias.asname
                for alias in node.names
                if alias.asname is not None and alias.name in known
            }
    return bound


def _identity_names(trees: dict[str, ast.Module]) -> frozenset[str]:
    """The identity spellings, plus every word the tree re-binds one to.

    Pooled over the whole tree before any module is judged, because a
    re-export lands in a module that may name the mint and is imported by one
    that may not, and a set of names would then be answered by handing the
    mint out under a third word. Grown hop by hop so an alias of an alias is
    the same value again, bounded by the module count — a chain can cross a
    module boundary at most once per module — and stopped as soon as a round
    binds nothing new.
    """
    known = CRITERION_IDENTITIES
    for _ in range(len(trees) + 1):
        grown = known
        for tree in trees.values():
            grown |= _bound_to(tree, grown)
        if grown == known:
            break
        known = grown
    return known


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
    """The negative clause, against every import spelling (KOD-639).

    The behavioural guard above replaces the mint in the module dict, so it
    sees a call reached as ``fire_spec.criterion_ref(...)`` and nothing of a
    caller that did ``from kodezart.domain.fire_spec import criterion_ref`` —
    which binds the function at import time, before any patch, and is the form
    this tree already uses elsewhere. ``CriterionRef`` is a ``NewType`` over
    ``str``, so a mint added on this path changes no value a test could read:
    the naming site is the only observable thing there is.

    Read statically, and as a census rather than a spot check, so a new naming
    site is reported wherever it lands and however it is imported.

    Two name sets, because the two assertions want different reach. The path
    clause takes EVERY identity spelling, each derived from the shipped object:
    constructing either ``NewType`` bare mints an identity just as surely as
    calling the mint does, and being a ``NewType`` it is even less observable —
    so a path module naming any of them has crossed the line, and none does
    today. That set is then grown by every word the tree binds one of those
    values to, pooled over the whole tree, so a re-export handing the mint out
    under a second name is the mint where it is called. The census takes the
    function alone, since the identity types are legitimately named across
    ``types/``, ``chains/criteria.py`` and ``domain/criteria.py`` and a total
    census over them would say nothing.

    What this does not see: a value fetched by reflection, and a wrapper that
    mints inside a function it hands back rather than binding the mint to a
    name — both of which are reflection-shaped and neither of which any module
    of this tree writes.
    """
    trees = _source_trees()
    identities = _naming(trees, _identity_names(trees))
    naming = _naming(trees, frozenset({CRITERION_MINT}))

    # Non-vacuous: the scan finds the sites there are, and the module that
    # defines the mint is derived rather than named.
    (definer,) = {
        path
        for path, tree in trees.items()
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == CRITERION_MINT
            for node in ast.walk(tree)
        )
    }
    assert definer == "domain/fire_spec.py"

    # The clause: no module of the question path names an identity mint at
    # all, under any spelling or any word the tree binds one to.
    assert identities.isdisjoint(RULING_PATH), sorted(
        identities.intersection(RULING_PATH)
    )
    # And the census over the mint function is total, so a naming site
    # anywhere else is reported too.
    assert naming == {definer} | MINT_CALLERS


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
