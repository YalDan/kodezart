"""The pre-loop question step, over the tracker port and a real repository.

The step is driven directly here, without a graph: what it puts on the
tracker, what a cold reader finds afterwards, and what it does on a second
pass are facts about the component, and a graph around it would only make
them harder to read.
"""

import pytest

from kodezart.adapters.git.service import SubprocessGitService
from kodezart.chains.criteria import TrackerCriteria
from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import RulingUnrecordedError
from kodezart.domain.prompt_variables import tracker_checks_section
from kodezart.domain.rulings import EMPTY_REGISTRY, render_ruling, ruling_marker
from kodezart.domain.ticket import format_fire_spec
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import RulingAnswer, RulingAuthor, RulingClass
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from tests.chains.test_native_fire import (
    DIRECT_OWED,
    SUBJECT,
    check_of,
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
    git,
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

REFUTED = {
    "verdict": "refuted",
    "evidence": "The landed text names a predicate this tree does not hold.",
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


# ---------------------------------------------------------------------------
# One fixture per class of defect, each on DIRECT_OWED's own Check line, as
# ambiguous_body() is for the reading class.  Every answer below is scripted:
# which class a defect falls in is the session's judgement, and no test here
# grades that judgement.
# ---------------------------------------------------------------------------

#: Two sentences of one Check that cannot both hold, and which one stands.
STANDING = "a failed item stays queued for a retry"
LOSING = "a failed item is dropped from the queue on failure"
CONTRADICTION_CHECK = f"{STANDING}, and {LOSING}"
CONTRADICTION_QUESTION = "Is a failed item kept for a retry or dropped?"


def contradiction_body() -> str:
    return criterion_body(DIRECT_OWED).replace(
        check_of(DIRECT_OWED), CONTRADICTION_CHECK
    )


def contradiction_answer(**changes) -> dict[str, object]:
    """One answer to the contradicting Check, with any field overridable.

    The defaults are merged under *changes* rather than passed as keywords,
    so a caller may replace any one of them — including with ``None``.
    """
    fields: dict[str, object] = {
        "question": CONTRADICTION_QUESTION,
        "rulingClass": "resolve_contradiction",
        "resolution": f"{STANDING}: the retry side stands.",
        "rejectedAlternative": (
            f"{LOSING}: the drop side loses, under which no retry can happen."
        ),
    }
    return one_answer(**{**fields, **changes})


#: An artifact the Check names but does not identify, and the precedent in
#: THIS tree it is to follow: policy.py is the one file at the base commit.
MODEL = "WorkflowsResponse"
CALL_SITE = "the adapter's list_workflows call"
PRECEDENT_FILE = "policy.py"
PRECEDENT = f"{PRECEDENT_FILE} — the frozen response model this tree already has"
ARTIFACT_CHECK = (
    "the workflows listing is validated as a typed response "
    "before the adapter returns it"
)
ARTIFACT_QUESTION = "Which response model does the listing validate against, and where?"


def artifact_body() -> str:
    return criterion_body(DIRECT_OWED).replace(check_of(DIRECT_OWED), ARTIFACT_CHECK)


def artifact_answer(**changes) -> dict[str, object]:
    """One answer naming the artifact and its precedent, with any field overridable."""
    fields: dict[str, object] = {
        "question": ARTIFACT_QUESTION,
        "rulingClass": "pin_artifact",
        "resolution": (
            f"The listing validates against {MODEL}, consumed at {CALL_SITE}."
        ),
        "rejectedAlternative": None,
        "repoEvidence": [PRECEDENT],
    }
    return one_answer(**{**fields, **changes})


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


class RecordingPrompts:
    """The provider port, recording which key each ask named.

    *override* stands in for one key's template only, so a prompt the
    executor received that carries it can only have come through the port
    under that key.
    """

    def __init__(self, inner, *, override: str | None = None) -> None:
        self._inner = inner
        self._override = override
        self.templates: list[PromptKey] = []
        self.policies: list[PromptKey] = []
        self.skills: list[PromptKey] = []

    def template_for(self, key: PromptKey) -> PromptTemplate:
        self.templates.append(key)
        if self._override is not None and key is PromptKey.FIRE_TIME_RULING:
            return PromptTemplate(
                key=key, source="fixture", body=self._override, bindings={}
            )
        return self._inner.template_for(key)

    def resolution_table(self):
        return self._inner.resolution_table()

    def declared_skills(self, key: PromptKey):
        return self._inner.declared_skills(key)

    def definitions(self):
        return self._inner.definitions()

    def system_prompt_append(self):
        return self._inner.system_prompt_append()

    def session_skills(self, key: PromptKey, configured):
        self.skills.append(key)
        return self._inner.session_skills(key, configured)

    def session_policy(self, key: PromptKey):
        self.policies.append(key)
        return self._inner.session_policy(key)


async def build(repository, executor, *, port=None, gate=None, prompts=None):
    """The step, its subject and the tracker Checks, wired as composition does."""
    repo, base = repository
    git_service = SubprocessGitService(remote="origin")
    workspace = Workspaces(git_service, FakeRepoCache(str(repo)))
    prompts = (
        prompts if prompts is not None else load_registry(default_set="claude-opus")
    )
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


async def test_the_question_prompt_is_the_providers_rendering_of_its_own_key(
    repository,
) -> None:
    """The role is resolved through the port under FIRE_TIME_RULING, not imported."""
    registry = load_registry(default_set="claude-opus")
    executor = Executor([[]])
    prompts = RecordingPrompts(registry)
    step, spec, current, _, _, _, repo_path, base = await build(
        repository, executor, prompts=prompts
    )

    await run(step, spec, current, repo_path, base)

    assert executor.question_prompts == [
        registry.template_for(PromptKey.FIRE_TIME_RULING).render(
            {
                "issue_key": SUBJECT,
                "task_md": format_fire_spec(spec)
                + "\n\n"
                + tracker_checks_section(current),
                "pinned_rulings": EMPTY_REGISTRY,
            }
        )
    ]
    assert PromptKey.FIRE_TIME_RULING in prompts.templates
    assert prompts.policies == [PromptKey.FIRE_TIME_RULING]
    assert prompts.skills == [PromptKey.FIRE_TIME_RULING]


async def test_a_template_the_provider_serves_for_the_key_is_what_reaches_the_pass(
    repository,
) -> None:
    """No import path can supply it: overriding the port's answer changes it."""
    sentinel = "A stand-in body the shipped set does not carry."
    executor = Executor([[]])
    prompts = RecordingPrompts(
        load_registry(default_set="claude-opus"), override=sentinel
    )
    step, spec, current, _, _, _, repo_path, base = await build(
        repository, executor, prompts=prompts
    )

    await run(step, spec, current, repo_path, base)

    assert executor.question_prompts == [sentinel]


async def test_a_record_the_tracker_accepted_but_does_not_list_is_not_pinned(
    repository,
) -> None:
    """A write that returned is not a record a later reader can find."""

    class HidingPort(type(tracker())):
        """Lists the record until it is told to stop, and not afterwards."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.hide = False

        async def list_comments(self, *, issue_key: str):
            listed = await super().list_comments(issue_key=issue_key)
            if not self.hide:
                return listed
            prefix = native_operation().marker_prefixes["ruling"]
            return tuple(
                comment
                for comment in listed
                if not comment.body.startswith(f"[{prefix}")
            )

    class HidesOnJudgement(Executor):
        """Stops the board listing the record once the judgement opens.

        The window reads the artifact it wrote before that judgement, so the
        window itself completes and the only read left to fail is the step's
        own cold one.
        """

        def __init__(self, sessions, *, port):
            super().__init__(sessions)
            self._port = port

        async def stream(self, **kwargs):
            schema = (kwargs.get("output_format") or {}).get("schema", {})
            if schema.get("title") == "WriteBackFinding":
                self._port.hide = True
            async for event in super().stream(**kwargs):
                yield event

    source = tracker(bodies={DIRECT_OWED: ambiguous_body()})
    port = HidingPort(
        issues=list(source.issues.values()),
        criteria_stage_label_key=source.criteria_stage_label_key,
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members=source.scope_label_members,
    )
    executor = HidesOnJudgement([[one_answer()]], port=port)
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=port
    )

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    # The write did happen, and so did the judgement of what it landed; what
    # failed is the step's own read-back of it.
    assert port.comment_writes
    assert len(executor.judged_artifacts) == 1


async def test_a_refuted_judgement_is_refused_under_the_fires_subject(
    repository,
) -> None:
    """One key names the fire; the issue that raised the question is the reason."""
    executor = Executor([[one_answer()]], findings=[dict(REFUTED)])
    step, spec, current, _, _, _, repo_path, base = await build(repository, executor)

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    assert DIRECT_OWED in caught.value.reason


async def test_a_gate_that_alters_the_answer_refuses_the_pin_as_the_fires_own(
    repository,
) -> None:
    """A redacted variant of a record is a different answer, so none is written.

    The record's worth is its exactness: the identity a later reader mints is
    the question's, and a body that came back changed would stand under that
    identity as the answer this fire pinned. So the write is refused with the
    step's own error, under the fire's subject, and the board keeps nothing.
    """

    class AlteringGate(PassThroughGate):
        """Returns a redacted body rather than the bytes it was given."""

        async def gate(
            self,
            *,
            content: str,
            visibility: RepoVisibility,
            shape: WriterShape,
            destination: OutboundDestination,
            content_class: ContentClass,
        ) -> GateDecision:
            await super().gate(
                content=content,
                visibility=visibility,
                shape=shape,
                destination=destination,
                content_class=content_class,
            )
            return GateDecision(
                verdict=GateVerdict.REDACTED, content=content.replace('"', "*", 1)
            )

    executor = Executor([[one_answer()]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, gate=AlteringGate()
    )

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    assert DIRECT_OWED in caught.value.reason
    # Nothing landed under the record's own marker, so there is no answer for
    # a later reader to find — and the gate was reached, so the refusal is
    # about what it returned and not about never having been asked.
    prefix = native_operation().marker_prefixes["ruling"]
    assert [
        write for write in port.comment_writes if write[1].startswith(f"[{prefix}")
    ] == []
    assert [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}")
    ] == []
    assert ContentClass.AUTHORED in gate.content_classes


@pytest.mark.parametrize("holder", [None, "  "], ids=["None", "blank"])
async def test_a_blank_holder_is_refused_before_any_session(repository, holder) -> None:
    """No parent job to write under: refused before any backend is reached."""
    executor = Executor([[one_answer()]])
    step, spec, current, _, port, _, repo_path, base = await build(repository, executor)

    with pytest.raises(NativeWriteRefusalError):
        await run(step, spec, current, repo_path, base, holder=holder)

    assert executor.calls == []
    assert port.leases == {}
    assert port.comment_writes == []


# ---------------------------------------------------------------------------
# Re-entry: the second pass finds its own earlier records and writes nothing.
# ---------------------------------------------------------------------------

SUBJECT_QUESTION = "Does the subject's own text settle the retry bound?"

#: Every write journal this double keeps, plus the live lease table: a pass
#: that wrote anything at all moves one of them.
JOURNALS = (
    "comment_writes",
    "issue_writes",
    "issue_creations",
    "workflow_writes",
    "restored_states",
    "queue_writes",
    "classification_writes",
    "lease_writes",
    "renewals",
)


def subject_answer(**changes) -> dict[str, object]:
    return one_answer(
        issueRef=SUBJECT,
        question=SUBJECT_QUESTION,
        rulingClass="pin_artifact",
        rejectedAlternative=None,
        **changes,
    )


#: The only keys of board_state() a record's own write moves: the comment,
#: the write journal it lands in, and the lease taken to write it.  Every
#: other key — the renewal journal and the live lease table included — must
#: be equal on both sides, because a pass acquires no per-issue claim and
#: releases the lease it took before it returns.
RECORD_WRITE = frozenset({"comments", "comment_writes", "lease_writes"})


def board_state(port) -> dict[str, object]:
    """Everything a second pass must leave exactly as it found it."""
    return {
        "bodies": {key: issue.body for key, issue in port.issues.items()},
        "states": {
            key: (issue.state_name, issue.state_kind)
            for key, issue in port.issues.items()
        },
        "comments": [
            (comment.issue_key, comment.comment_key, comment.body)
            for comment in port.comments
        ],
        "leases": dict(port.leases),
        **{name: list(getattr(port, name)) for name in JOURNALS},
    }


async def pinned_record(port, answer, *, before):
    """The record one scripted answer landed, as a cold reader finds it.

    Asserts what a pass of any class must satisfy: exactly one comment under
    the record prefix, on the issue the answer addresses; the minted identity
    in its marker line; the class named in the body; machine authorship and
    every answered field intact on a cold read-back; the parsed record
    rendering back to the bytes on the board; and nothing on the board moved
    except that comment and the lease taken to write it.  The caller then
    asserts the clauses its own class owes.
    """
    prefixes = native_operation().marker_prefixes
    pinned = [
        comment
        for comment in port.comments
        if comment.body.startswith(f"[{prefixes['ruling']}")
    ]
    assert [comment.issue_key for comment in pinned] == [answer["issueRef"]]
    identity = mint_ruling_id(issue_ref=answer["issueRef"], question=answer["question"])
    assert pinned[0].body.splitlines()[0] == ruling_marker(
        ruling_id=identity, lane_key=SUBJECT, marker_prefixes=prefixes
    )
    # The rendered form carries the class by name.
    answered_class = answer["rulingClass"]
    assert f'"rulingClass": "{answered_class}"' in pinned[0].body
    ((_, record),) = await cold_records(port, answer["issueRef"])
    assert record.ruling_id == identity
    assert record.authored_by is RulingAuthor.MACHINE
    # Every field the session answered survives render and read-back.
    assert (
        record.model_dump(exclude={"ruling_id", "authored_by", "protected_tests"})
        == RulingAnswer.model_validate(answer).model_dump()
    )
    # And what the reader parsed renders back to the bytes on the board.
    assert (
        render_ruling(ruling=record, lane_key=SUBJECT, marker_prefixes=prefixes)
        == pinned[0].body
    )
    # Nothing but the record moved: no body, no state, no other journal.
    after = board_state(port)
    assert {key: value for key, value in after.items() if key not in RECORD_WRITE} == {
        key: value for key, value in before.items() if key not in RECORD_WRITE
    }
    return record


@pytest.mark.parametrize("second", ["the same answers", "a different resolution"])
async def test_a_second_pass_over_one_fixture_writes_nothing(
    repository, second
) -> None:
    """The identity is the question's, so a later answer to it is dropped."""
    repeat = (
        [one_answer(), subject_answer()]
        if second == "the same answers"
        else [one_answer(resolution="A later, different answer."), subject_answer()]
    )
    executor = Executor([[one_answer(), subject_answer()], repeat])
    step, spec, current, _, port, _, repo_path, base = await build(repository, executor)
    before_bodies = {key: issue.body for key, issue in port.issues.items()}

    await run(step, spec, current, repo_path, base)
    after_first = board_state(port)
    judged = len(executor.judged_artifacts)

    await run(step, spec, current, repo_path, base)

    assert board_state(port) == after_first
    assert after_first["bodies"] == before_bodies
    # One record per question, each on the issue whose text raised it.
    records = [comment for comment in port.comments if comment.body.startswith("[")]
    assert sorted(comment.issue_key for comment in records) == sorted(
        {DIRECT_OWED, SUBJECT}
    )
    # The second pass judged nothing, because it wrote nothing to judge.
    assert len(executor.judged_artifacts) == judged == 2
    # It did ask again, and it was shown what the board already carries.
    assert len(executor.question_prompts) == 2
    assert RESOLUTION in executor.question_prompts[1]
    # The record still says what the first pass pinned.
    ((_, record),) = await cold_records(port, DIRECT_OWED)
    assert record.resolution == RESOLUTION


# ---------------------------------------------------------------------------
# One answer of each class of defect, through the step and off the board again.
# ---------------------------------------------------------------------------


async def test_a_self_contradiction_is_answered_naming_which_side_stands_and_loses(
    repository,
) -> None:
    """Two sentences of one Check cannot both hold; the record says which one does.

    Answered here rather than deferred: after one pass the answer is on the
    tracker, before any loop, and nothing was raised anywhere else (KOD-628).
    """
    answer = contradiction_answer()
    executor = Executor([[answer], [answer]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, port=tracker(bodies={DIRECT_OWED: contradiction_body()})
    )
    before = board_state(port)

    assert await run(step, spec, current, repo_path, base) is None

    record = await pinned_record(port, answer, before=before)
    assert record.ruling_class is RulingClass.RESOLVE_CONTRADICTION
    # The side that stands is the resolution and only the resolution; the side
    # that loses is the rejected alternative and only that.
    assert STANDING in record.resolution and LOSING not in record.resolution
    assert LOSING in record.rejected_alternative
    assert STANDING not in record.rejected_alternative
    # The session was shown the contradicting text, and what landed was gated
    # as content this run authored.
    assert CONTRADICTION_CHECK in executor.question_prompts[0]
    assert ContentClass.AUTHORED in gate.content_classes
    # A second pass over the same fixture writes nothing and judges nothing,
    # and is shown the answer the first pass pinned.
    after_first = board_state(port)
    await run(step, spec, current, repo_path, base)
    assert board_state(port) == after_first
    assert len(executor.judged_artifacts) == 1
    assert len(executor.question_prompts) == 2
    assert STANDING in executor.question_prompts[1]


async def test_a_contradiction_answer_with_no_losing_side_is_refused_before_any_lease(
    repository,
) -> None:
    """Naming the side that loses is the step's requirement, not the script's habit.

    An answer of this class with no rejected alternative is no valid record,
    so it is refused while the arithmetic is still being done — before the
    surfaces are leased, before the gate is reached, and before anything is
    written for a later reader to find.
    """
    executor = Executor([[contradiction_answer(rejectedAlternative=None)]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, port=tracker(bodies={DIRECT_OWED: contradiction_body()})
    )

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    assert "not a valid record" in caught.value.reason
    assert DIRECT_OWED in caught.value.reason
    assert port.comment_writes == []
    assert port.lease_writes == []
    assert port.leases == {}
    assert executor.judged_artifacts == []
    assert len(executor.question_prompts) == 1
    assert gate.content_classes == []


async def test_an_invented_artifact_pins_its_model_call_site_and_in_repo_precedent(
    repository,
) -> None:
    """The Check names an artifact it does not identify; the record identifies it.

    "In-repo" is a checked fact and not a string: the precedent names a file
    the tree actually carries at the base the pass stood at (KOD-627).  The
    precedent rides in the record's evidence as data — the instruction to name
    one is prose in the shipped role, and no model validator requires it.
    """
    answer = artifact_answer()
    executor = Executor([[answer], [answer]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=tracker(bodies={DIRECT_OWED: artifact_body()})
    )
    before = board_state(port)

    assert await run(step, spec, current, repo_path, base) is None

    record = await pinned_record(port, answer, before=before)
    assert record.ruling_class is RulingClass.PIN_ARTIFACT
    assert MODEL in record.resolution and CALL_SITE in record.resolution
    assert record.rejected_alternative is None
    assert record.repo_evidence == (PRECEDENT,)
    # Non-vacuous: the Check itself names neither the model nor the call site,
    # so neither could have reached the loop by riding in the issue's text.
    assert MODEL not in artifact_body() and CALL_SITE not in artifact_body()
    # The precedent's file is in the tree at the base the pass read.
    at_base = (await git(repo_path, "ls-tree", "--name-only", base)).splitlines()
    assert PRECEDENT_FILE in at_base
    # A second pass over the same fixture writes nothing and judges nothing.
    after_first = board_state(port)
    await run(step, spec, current, repo_path, base)
    assert board_state(port) == after_first
    assert len(executor.judged_artifacts) == 1
