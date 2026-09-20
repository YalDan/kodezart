"""The pre-loop question step, over the tracker port and a real repository.

The step is driven directly here, without a graph: what it puts on the
tracker, what a cold reader finds afterwards, and what it does on a second
pass are facts about the component, and a graph around it would only make
them harder to read.
"""

import pytest

from kodezart.adapters.git.service import SubprocessGitService
from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.errors import RulingUnrecordedError
from kodezart.domain.rulings import ruling_marker
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import RulingAuthor
from kodezart.types.domain.gating import ContentClass, RepoVisibility
from tests.chains.test_native_fire import (
    DIRECT_OWED,
    SUBJECT,
    criterion_body,
    native_operation,
    tracker,
)
from tests.chains.test_organize import result
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeChangePersister,
    FakeRepoCache,
    PassThroughGate,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_native_amendments import (
    Workspaces,
    repository,
)

__all__ = ["repository"]

HOLDER = "actual-parent-job"

#: A Check two readings fit, and the answer that pins one of them.
AMBIGUOUS_CHECK = "the run is finished when the queue is drained"
QUESTION = "Is a queue holding only failed items drained?"
RESOLUTION = "A queue holding failed items is not drained."
REJECTED = "Treating failed items as drained, under which the run cannot end."
EVIDENCE = "policy.py — the queue predicate this tree already has"

HOLDS = {
    "verdict": "holds",
    "evidence": "Read the landed record against the base commit.",
    "cited_refs": ["policy.py"],
}


def ambiguous_body() -> str:
    return criterion_body(DIRECT_OWED).replace(
        f"the check {DIRECT_OWED} states", AMBIGUOUS_CHECK
    )


def one_answer(**changes) -> dict[str, object]:
    fields: dict[str, object] = {
        "issueRef": DIRECT_OWED,
        "question": QUESTION,
        "rulingClass": "pin_reading",
        "resolution": RESOLUTION,
        "rejectedAlternative": REJECTED,
        "repoEvidence": [EVIDENCE],
    }
    fields.update(changes)
    return fields


class Executor:
    """Only the agent boundary is scripted; the port and the trees are real."""

    def __init__(self, sessions, *, findings=None):
        #: One list of answers per pass through the step, in order.
        self.sessions = [list(answers) for answers in sessions]
        self.findings = list(findings or [])
        self.calls: list[dict[str, object]] = []
        self.question_prompts: list[str] = []
        self.question_sessions: list[dict[str, object]] = []
        self.judged_artifacts: list[str] = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        schema = (kwargs.get("output_format") or {}).get("schema", {})
        title = schema.get("title")
        if title == "RulingOutput":
            self.question_prompts.append(kwargs["prompt"])
            self.question_sessions.append(kwargs)
            answers = self.sessions.pop(0) if self.sessions else []
            payload: dict[str, object] = {"rulings": answers}
        elif title == "WriteBackFinding":
            self.judged_artifacts.append(kwargs["prompt"])
            payload = self.findings.pop(0) if self.findings else dict(HOLDS)
        else:
            raise AssertionError(f"the question step opened no session for {title!r}")
        yield result(structured_output=payload)


async def build(repository, executor, *, port=None, gate=None):
    """The step, its subject and the tracker Checks, wired as composition does."""
    repo, base = repository
    git_service = SubprocessGitService(remote="origin")
    workspace = Workspaces(git_service, FakeRepoCache(str(repo)))
    prompts = load_registry(default_set="claude-opus")
    service = AgentService(
        executor=executor,
        workspace=workspace,
        persister=FakeChangePersister(),
        git_base_url="https://example.invalid",
    )
    port = port if port is not None else tracker(bodies={DIRECT_OWED: ambiguous_body()})
    gate = gate if gate is not None else PassThroughGate()
    step = FireTimeRulings(
        tracker=port,
        operation=native_operation(),
        runner=service,
        workspace=workspace,
        git=git_service,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        gate=gate,
        lease_seconds=900,
    )
    criteria = TrackerCriteria(tracker=port)
    spec = await criteria.read_spec(issue_key=SUBJECT)
    current = await criteria.read_current(spec=spec, held=None)
    return step, spec, current, workspace, port, gate, str(repo), base


async def run(step, spec, current, repo_path, base, *, holder=HOLDER):
    return await step.rule(
        spec=spec,
        criteria=current,
        repo_path=repo_path,
        repo_url=None,
        ref=base,
        cache_key="fixture-question-step",
        holder=holder,
        visibility=RepoVisibility.PUBLIC,
    )


def cold_records(port, issue_key):
    return RulingRecordReader(tracker=port, operation=native_operation()).read_issue(
        issue_key=issue_key
    )


async def test_an_ambiguous_check_yields_one_pinned_answer_naming_both_readings(
    repository,
) -> None:
    """One record, on the issue whose own text raised the question."""
    executor = Executor([[one_answer()]])
    step, spec, current, workspace, port, gate, repo_path, base = await build(
        repository, executor
    )
    bodies_before = {key: issue.body for key, issue in port.issues.items()}

    assert await run(step, spec, current, repo_path, base) is None

    prefix = native_operation().marker_prefixes["ruling"]
    pinned = [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}")
    ]
    assert len(pinned) == 1
    assert pinned[0].issue_key == DIRECT_OWED
    identity = mint_ruling_id(issue_ref=DIRECT_OWED, question=QUESTION)
    assert pinned[0].body.splitlines()[0] == ruling_marker(
        ruling_id=identity,
        lane_key=SUBJECT,
        marker_prefixes=native_operation().marker_prefixes,
    )
    # Read back by a reader that saw none of this run's state.
    ((_, record),) = await cold_records(port, DIRECT_OWED)
    assert record.ruling_id == identity
    assert record.resolution == RESOLUTION
    assert record.rejected_alternative == REJECTED
    assert record.authored_by is RulingAuthor.MACHINE
    # No issue body moved: the record is a comment and nothing else.
    assert {key: issue.body for key, issue in port.issues.items()} == bodies_before
    # The text was gated as content this run authored, and the independent
    # judgement was shown the body that actually landed.
    assert ContentClass.AUTHORED in gate.content_classes
    assert len(executor.judged_artifacts) == 1
    assert RESOLUTION in executor.judged_artifacts[0]
    # The step opened exactly one tree of its own, and never a branch.
    assert [call["create_branch"] for _, call in workspace.acquired] == [False, False]
    assert all(call.get("branch_name") is None for _, call in workspace.acquired)


async def test_a_record_the_tracker_accepted_but_does_not_list_is_not_pinned(
    repository,
) -> None:
    """A write that returned is not a record a later reader can find."""

    class HidingPort(type(tracker())):
        async def list_comments(self, *, issue_key: str):
            listed = await super().list_comments(issue_key=issue_key)
            prefix = native_operation().marker_prefixes["ruling"]
            return tuple(
                comment
                for comment in listed
                if not comment.body.startswith(f"[{prefix}")
            )

    source = tracker(bodies={DIRECT_OWED: ambiguous_body()})
    port = HidingPort(
        issues=list(source.issues.values()),
        criteria_stage_label_key=source.criteria_stage_label_key,
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members=source.scope_label_members,
    )
    executor = Executor([[one_answer()]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=port
    )

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    # The write did happen; what failed is the confirmation of it.
    assert port.comment_writes
