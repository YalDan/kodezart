"""The question step's place in the native fire graph, and what it opens.

Placement is the whole subject: the step stands between the re-validation
that captures the subject and the loop that executes it, it is the only way
into that loop, and a lane entered to deliver never reaches it.  The authored
graph is untouched, and no generation node runs on either path through here.
"""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.core.errors import NoStructuredOutputError, TrackerUnavailableError
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import FireSpecEntryError, SurfaceLeaseError
from kodezart.types.domain.agent import ResultEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import TrackerComment
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    DIRECT_OWED_TOO,
    GENERATION_NODES,
    NESTED_DONE,
    NESTED_OWED,
    OWED_KEYS,
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
from tests.fakes import FakeGitService, FakeTrackerPort, FakeWorkspaceProvider

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


def is_record(comment: TrackerComment) -> bool:
    return comment.body.startswith(f"[{RULING_PREFIX}")


class WriteFails(FakeTrackerPort):
    """The record's own write fails; every other write of the board is fine."""

    async def upsert_comment(self, *, target, marker, body, holder=None, expected=None):
        if marker.startswith(f"[{RULING_PREFIX}"):
            raise TrackerUnavailableError("the record write is unavailable")
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


class AltersRecord(FakeTrackerPort):
    """The write returns and the text a later reader finds is not the text sent."""

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
    executor = NativeExecutor([])
    executor.question_answers = [{"rulings": [ANSWER]}]
    if shape == "judged_refuted":
        executor.findings = [dict(REFUTED)]
        return variant(FakeTrackerPort), executor
    if shape == "write_fails":
        return variant(WriteFails), executor
    if shape == "lease_refused":
        return variant(LeaseRefused), executor
    if shape == "read_back_changed":
        return variant(AltersRecord), executor
    port = variant(HidesRecord)
    port.hide = True
    return port, executor


@pytest.mark.parametrize(
    "shape",
    [
        "write_fails",
        "lease_refused",
        "judged_refuted",
        "read_back_empty",
        "read_back_changed",
    ],
)
async def test_an_unconfirmed_pin_halts_before_the_loop_with_its_own_outcome(
    shape,
) -> None:
    """Five ways to fail, one terminal: the loop was never entered."""
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
