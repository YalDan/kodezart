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
from kodezart.domain.fire_spec import DELIVERABLES_SECTION, deliverables_section
from kodezart.domain.prompt_variables import tracker_checks_section
from kodezart.domain.rulings import (
    EMPTY_REGISTRY,
    escalation_marker,
    render_ruling,
    ruling_marker,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import (
    Ruling,
    RulingAnswer,
    RulingAuthor,
    RulingClass,
)
from kodezart.types.domain.escalation import DeliverableEscalation
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    TrackerAggregate,
    WriterShape,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind
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
#: The same question in different words: a new address, and the one it names.
RESTATED_CONTRADICTION_QUESTION = (
    "Does a failed item stay in the queue for a retry, or leave it?"
)


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


#: The one deliverable the subject's own text states, and a consequence it
#: does not: the retry side stands, but a second queue was never in scope.
STATED_DELIVERABLE = "bound a failed item's retries on the existing queue predicate"
EXCESS_DELIVERABLE = "a second queue that holds failed items"


def subject_body(*, deliverables=(STATED_DELIVERABLE,)) -> str:
    """The subject's own text with the section the excess arm reads."""
    items = "".join(f"- {item}\n" for item in deliverables)
    return f"the subject's own text\n\n## {DELIVERABLES_SECTION}\n{items}"


def deliverable_board(*, subject=None):
    """The fixture board: the contradicting Check, and the stated section."""
    bodies = {DIRECT_OWED: contradiction_body()}
    if subject is not None:
        bodies[SUBJECT] = subject
    return tracker(bodies=bodies)


def raised_comments(port):
    """Every comment on the board under the operation's escalation prefix."""
    prefix = native_operation().marker_prefixes["escalation"]
    return [
        comment for comment in port.comments if comment.body.startswith(f"[{prefix}")
    ]


def pinned_bodies(port, prefixes):
    """Every pinned comment on the board as an addressable, byte-exact triple."""
    return [
        (comment.issue_key, comment.comment_key, comment.body)
        for comment in port.comments
        if comment.body.startswith(f"[{prefixes['ruling']}")
    ]


def raised_body(comment):
    """The escalation's own payload, decoded out of its fenced framing."""
    _, separator, payload = comment.body.partition("\n```json\n")
    assert separator and payload.endswith("\n```")
    return DeliverableEscalation.model_validate_json(payload[: -len("\n```")])


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


#: A premise the tree at the base contradicts: newer.py exists only in the
#: commit after it, so the helper this Check rests on is not there to build on.
ABSENT_MODULE = "newer.py"
PREMISE_CHECK = (
    f"the retry helper in {ABSENT_MODULE} bounds every failed item's attempts"
)
PREMISE_QUESTION = "Which helper bounds a failed item's attempts?"
REGROUNDED = (
    f"{PRECEDENT_FILE} holds the queue predicate; there is no {ABSENT_MODULE} at "
    "this base, so the bound is built on that predicate."
)
REGROUND_EVIDENCE = f"{PRECEDENT_FILE} — the only module at this base"


def premise_body() -> str:
    return criterion_body(DIRECT_OWED).replace(check_of(DIRECT_OWED), PREMISE_CHECK)


def premise_answer(**changes) -> dict[str, object]:
    """One answer re-grounding the Check at the base, with any field overridable."""
    fields: dict[str, object] = {
        "question": PREMISE_QUESTION,
        "rulingClass": "reground_premise",
        "resolution": REGROUNDED,
        "rejectedAlternative": None,
        "repoEvidence": [REGROUND_EVIDENCE],
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


async def build(
    repository, executor, *, port=None, gate=None, prompts=None, operation=None
):
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
        operation=native_operation() if operation is None else operation,
        runner=service,
        workspace=workspace,
        git=git_service,
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
        gate=gate,
        lease_seconds=900,
    )
    criteria = TrackerCriteria(tracker=port)
    spec, current = await criteria.read_entry(issue_key=SUBJECT)
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
            aggregates: tuple[TrackerAggregate, ...],
        ) -> GateDecision:
            await super().gate(
                content=content,
                visibility=visibility,
                shape=shape,
                destination=destination,
                content_class=content_class,
                aggregates=aggregates,
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
    "claim_writes",
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
#: other key — the claim journal, the renewal journal and the live lease
#: table included — must be equal on both sides, because a pass acquires no
#: per-issue claim and releases the lease it took before it returns.  The
#: claim journal, not the live claim table: it outlives the release, so a
#: claim taken and given back inside one pass is still visible there.
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
    # Every field the session answered survives render and read-back. The
    # answer's own supersession field is held out on both sides because the
    # record carries the minted identity rather than the words: the pointer
    # is asserted next, from the same pair. The deliverable the answer falls
    # under is held out because the record carries no such field at all — it
    # adds nothing a record needs, and an excess one is never built.
    assert "deliverable" not in Ruling.model_fields
    assert record.model_dump(
        exclude={"ruling_id", "authored_by", "protected_tests", "supersedes"}
    ) == RulingAnswer.model_validate(answer).model_dump(
        exclude={"supersedes_question", "deliverable"}
    )
    replaced = answer.get("supersedesQuestion")
    assert record.supersedes == (
        None
        if replaced is None
        else mint_ruling_id(issue_ref=answer["issueRef"], question=replaced)
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
    # And the comment journal — held out of that comparison, because the
    # record itself lands there — grew by exactly the record's own comment,
    # so the pass wrote no other comment on any issue.
    assert after["comment_writes"] == [
        *before["comment_writes"],
        (pinned[0].comment_key, pinned[0].body),
    ]
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


async def test_an_answer_naming_a_deliverable_the_subject_states_is_pinned_as_usual(
    repository,
) -> None:
    """The positive control: what the section already names is pinned (KOD-629).

    Without this the arm below could refuse every answer that names anything
    and still look right.
    """
    answer = contradiction_answer(deliverable=STATED_DELIVERABLE)
    executor = Executor([[answer]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, port=deliverable_board(subject=subject_body())
    )
    before = board_state(port)

    assert await run(step, spec, current, repo_path, base) is None

    record = await pinned_record(port, answer, before=before)
    assert record.ruling_class is RulingClass.RESOLVE_CONTRADICTION
    assert raised_comments(port) == []
    assert ContentClass.AUTHORED in gate.content_classes


async def test_a_deliverable_the_subject_does_not_state_is_raised_and_not_pinned(
    repository,
) -> None:
    """Refused as an answer, raised on the issue whose text raised it (KOD-629).

    Nothing is pinned at all on such a pass, so the tracker carries the
    question rather than half a record: exactly one comment under the
    escalation prefix and none under the record prefix.
    """
    answer = contradiction_answer(deliverable=EXCESS_DELIVERABLE)
    executor = Executor([[answer]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, port=deliverable_board(subject=subject_body())
    )

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    assert DIRECT_OWED in caught.value.reason
    (raise_comment,) = raised_comments(port)
    assert raise_comment.issue_key == DIRECT_OWED
    prefixes = native_operation().marker_prefixes
    identity = mint_ruling_id(issue_ref=DIRECT_OWED, question=CONTRADICTION_QUESTION)
    assert raise_comment.body.splitlines()[0] == escalation_marker(
        ruling_id=identity, lane_key=SUBJECT, marker_prefixes=prefixes
    )
    # Nothing is pinned: zero comments under the record prefix.
    assert [
        comment
        for comment in port.comments
        if comment.body.startswith(f"[{prefixes['ruling']}")
    ] == []
    assert await cold_records(port, DIRECT_OWED) == ()
    # The raise carries what a person needs in order to answer it.
    raised = raised_body(raise_comment)
    assert raised.issue_ref == DIRECT_OWED
    assert raised.question == CONTRADICTION_QUESTION
    assert raised.deliverable == EXCESS_DELIVERABLE
    assert raised.stated == (STATED_DELIVERABLE,)
    # The text was gated as content this run authored, and judged as written.
    assert ContentClass.AUTHORED in gate.content_classes
    assert len(executor.judged_artifacts) == 1
    assert EXCESS_DELIVERABLE in executor.judged_artifacts[0]


async def test_the_raise_lands_on_a_leased_surface_before_the_refusal(
    repository, monkeypatch
) -> None:
    """The write is inside the window and the refusal is after it (KOD-629).

    The window is asserted at the moment of the write: the port's own live
    lease table is read as the comment is being recorded, and the surface
    written to has to be in it under this pass's holder. Reading that table
    afterwards would say nothing — the granted set is the same set whether the
    write happened inside the window or after it closed.
    """
    answer = contradiction_answer(deliverable=EXCESS_DELIVERABLE)
    executor = Executor([[answer]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=deliverable_board(subject=subject_body())
    )
    held_at_write: list[frozenset[WritableSurface]] = []
    recorded = port.upsert_comment

    async def upsert_comment(**fields):
        held_at_write.append(
            frozenset(
                surface
                for surface, lease in port.leases.items()
                if lease.holder == HOLDER
            )
        )
        return await recorded(**fields)

    monkeypatch.setattr(port, "upsert_comment", upsert_comment)

    with pytest.raises(RulingUnrecordedError):
        await run(step, spec, current, repo_path, base)

    (raise_comment,) = raised_comments(port)
    surface = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=DIRECT_OWED),
        marker=raise_comment.body.splitlines()[0],
    )
    # The lease over that surface was live, under this holder, as it was written.
    (held,) = held_at_write
    assert surface in held
    assert surface in {held for lease in port.lease_writes for held in lease.surfaces}
    # And the window closed before the refusal reached the caller.
    assert port.leases == {}
    # Non-vacuous: the lease was taken by this pass's own holder.
    assert [lease.holder for lease in port.lease_writes] == [HOLDER] * len(
        port.lease_writes
    )


async def test_an_answer_outside_the_fire_is_refused_before_any_raise_is_written(
    repository,
) -> None:
    """The arithmetic runs before the raise, so no issue this fire cannot address.

    An answer addressed outside the fire that also names work beyond what the
    subject states is refused for being outside the fire — the refusal it would
    otherwise have earned is never written, because a raise on an issue this
    fire cannot address is text whose reader cannot address it back.
    """
    outside = "EXT/999"
    answer = contradiction_answer(issueRef=outside, deliverable=EXCESS_DELIVERABLE)
    executor = Executor([[answer]])
    step, spec, current, _, port, gate, repo_path, base = await build(
        repository, executor, port=deliverable_board(subject=subject_body())
    )
    assert outside not in {spec.subject, *(str(ref) for ref in spec.criteria)}

    with pytest.raises(RulingUnrecordedError) as caught:
        await run(step, spec, current, repo_path, base)

    assert caught.value.issue_key == SUBJECT
    assert outside in caught.value.reason
    assert "is not a member of this fire" in caught.value.reason
    # Nothing was written, leased or gated on the way to that refusal.
    assert raised_comments(port) == []
    assert port.comment_writes == []
    assert port.lease_writes == []
    assert gate.content_classes == []
    # Non-vacuous: the same answer inside the fire is raised rather than refused.
    inside = Executor([[contradiction_answer(deliverable=EXCESS_DELIVERABLE)]])
    step, spec, current, _, raised_port, _, repo_path, base = await build(
        repository, inside, port=deliverable_board(subject=subject_body())
    )
    with pytest.raises(RulingUnrecordedError):
        await run(step, spec, current, repo_path, base)
    assert len(raised_comments(raised_port)) == 1


async def test_a_fire_under_an_operation_that_configures_no_escalation_prefix_pins(
    repository,
) -> None:
    """The escalation prefix is needed only by the arm that escalates (KOD-629).

    An operation that configures no escalation prefix can still answer every
    open question, because an answer that stays inside what the subject states
    raises nothing: the prefix is resolved on the escalating arm rather than at
    the top, so such an operation fires normally instead of refusing every pass.
    """
    prefixes = {
        purpose: prefix
        for purpose, prefix in native_operation().marker_prefixes.items()
        if purpose != "escalation"
    }
    operation = OperationConfig(
        operation_name="native-fixture-no-escalation",
        workspace="fixture",
        marker_prefixes=prefixes,
        issue_labels={"decision": "decision"},
    )
    assert "escalation" not in operation.marker_prefixes
    assert "ruling" in operation.marker_prefixes

    # An answer naming no deliverable: nothing to raise, so nothing needs it.
    executor = Executor([[contradiction_answer()]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository,
        executor,
        port=deliverable_board(subject=subject_body()),
        operation=operation,
    )

    assert await run(step, spec, current, repo_path, base) is None

    # The pass pinned its answer, under this operation's own record prefix.
    (pinned,) = pinned_bodies(port, operation.marker_prefixes)
    assert pinned[0] == DIRECT_OWED
    identity = mint_ruling_id(issue_ref=DIRECT_OWED, question=CONTRADICTION_QUESTION)
    assert pinned[2].splitlines()[0] == ruling_marker(
        ruling_id=identity,
        lane_key=SUBJECT,
        marker_prefixes=operation.marker_prefixes,
    )


async def test_a_subject_with_no_stated_deliverables_raises_any_answer_that_names_one(
    repository,
) -> None:
    """Nothing stated and nothing named are one answer, so naming one exceeds it.

    The subject body is the landed bare text with no such section at all.
    """
    answer = contradiction_answer(deliverable=STATED_DELIVERABLE)
    executor = Executor([[answer]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=deliverable_board()
    )
    assert deliverables_section(spec.body) == ()

    with pytest.raises(RulingUnrecordedError):
        await run(step, spec, current, repo_path, base)

    (raise_comment,) = raised_comments(port)
    assert raised_body(raise_comment).stated == ()
    assert raised_body(raise_comment).deliverable == STATED_DELIVERABLE
    # Non-vacuous: an answer naming nothing on the same board is pinned.
    quiet = Executor([[contradiction_answer()]])
    step, spec, current, _, quiet_port, _, repo_path, base = await build(
        repository, quiet, port=deliverable_board()
    )
    assert await run(step, spec, current, repo_path, base) is None
    assert raised_comments(quiet_port) == []


async def test_a_second_pass_leaves_one_raised_question_not_two(
    repository,
) -> None:
    """The occurrence key is the question's own identity, so the raise upserts."""
    answer = contradiction_answer(deliverable=EXCESS_DELIVERABLE)
    executor = Executor([[answer], [answer]])
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=deliverable_board(subject=subject_body())
    )

    for _ in range(2):
        with pytest.raises(RulingUnrecordedError):
            await run(step, spec, current, repo_path, base)

    assert len(raised_comments(port)) == 1
    identity = mint_ruling_id(issue_ref=DIRECT_OWED, question=CONTRADICTION_QUESTION)
    assert raised_comments(port)[0].body.splitlines()[0] == escalation_marker(
        ruling_id=identity,
        lane_key=SUBJECT,
        marker_prefixes=native_operation().marker_prefixes,
    )
    # Non-vacuous: the second pass did open its own question session.
    assert len(executor.question_prompts) == 2


async def test_a_restated_question_leaves_the_earlier_record_readable_and_unedited(
    repository,
) -> None:
    """The restatement is a second record that addresses the first (KOD-635).

    Driven through the step twice over one board, so the second pass reads the
    first pass's own record off the tracker: what makes the pointer followable
    is that the identity it names is the one the earlier record is addressed
    by, and what makes the earlier answer readable is that its comment is the
    same comment, byte for byte, afterwards.
    """
    earlier_answer = contradiction_answer()
    later_answer = contradiction_answer(
        question=RESTATED_CONTRADICTION_QUESTION,
        supersedesQuestion=CONTRADICTION_QUESTION,
    )
    # The same question, arriving with a pointer to a question no record on
    # this board answers, for the replay the fourth pass drives.
    stale_pointer_answer = contradiction_answer(
        question=RESTATED_CONTRADICTION_QUESTION,
        supersedesQuestion="Which queue predicate did the earlier reading name?",
    )
    executor = Executor(
        [[earlier_answer], [later_answer], [later_answer], [stale_pointer_answer]]
    )
    step, spec, current, _, port, _, repo_path, base = await build(
        repository, executor, port=tracker(bodies={DIRECT_OWED: contradiction_body()})
    )

    assert await run(step, spec, current, repo_path, base) is None
    prefixes = native_operation().marker_prefixes
    first_pass = pinned_bodies(port, prefixes)
    assert len(first_pass) == 1

    assert await run(step, spec, current, repo_path, base) is None

    records = {
        record.ruling_id: record for _, record in await cold_records(port, DIRECT_OWED)
    }
    earlier = mint_ruling_id(issue_ref=DIRECT_OWED, question=CONTRADICTION_QUESTION)
    later = mint_ruling_id(
        issue_ref=DIRECT_OWED, question=RESTATED_CONTRADICTION_QUESTION
    )
    assert set(records) == {earlier, later}
    # The new record names the one it replaces, and the earlier one names none.
    assert records[later].supersedes == earlier
    assert records[earlier].supersedes is None
    # Both answers stand: the earlier comment is the same comment, unedited.
    pinned = pinned_bodies(port, prefixes)
    assert first_pass[0] in pinned
    assert len(pinned) == 2
    # The second pass was shown the first answer and did write its own.
    assert CONTRADICTION_QUESTION in executor.question_prompts[1]
    assert len(executor.judged_artifacts) == 2

    # A third pass replaying the later answer owes nothing, and writes nothing.
    assert await run(step, spec, current, repo_path, base) is None
    assert pinned_bodies(port, prefixes) == pinned

    # A fourth pass replays the same question carrying a pointer to a question
    # the tracker carries no answer for.  The recorded-identity check runs
    # before the pointer is minted, so the replay is dropped with the answer
    # rather than refused: a pass over an answer already on record owes
    # nothing, whatever the pointer it arrives with names.
    assert await run(step, spec, current, repo_path, base) is None
    assert pinned_bodies(port, prefixes) == pinned
    assert len(executor.question_prompts) == 4


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


async def test_a_false_premise_is_regrounded_at_the_base_neither_closed_nor_crossed_off(
    repository,
) -> None:
    """The Check rests on a file the base does not have; the answer re-grounds it.

    Neither negative is a comment.  Not closed: no state and no description
    was written on any issue, and the two the answer concerns are still open
    by kind.  Not crossed off: the criterion set read again after the pass is
    the same set the step was handed, with DIRECT_OWED's text unchanged — so
    the answer re-grounds the work rather than declaring it already done.

    What is NOT claimed here is that the step grades any Check against the
    base tree: it opens its trees at the base and reads them, and the
    arithmetic over what a base already satisfies belongs elsewhere (KOD-626).
    """
    answer = premise_answer()
    executor = Executor([[answer], [answer]])
    step, spec, current, workspace, port, _, repo_path, base = await build(
        repository, executor, port=tracker(bodies={DIRECT_OWED: premise_body()})
    )
    before = board_state(port)

    assert await run(step, spec, current, repo_path, base) is None

    record = await pinned_record(port, answer, before=before)
    assert record.ruling_class is RulingClass.REGROUND_PREMISE
    assert record.resolution == REGROUNDED
    assert record.rejected_alternative is None
    # Both trees the pass opened stood at the base, and the premise is false
    # there: the Check on the board rests on the module the base lacks, that
    # module is not in the tree, and the one the answer re-grounds on is.
    assert [call["ref"] for _, call in workspace.acquired] == [base, base]
    assert ABSENT_MODULE in premise_body()
    at_base = (await git(repo_path, "ls-tree", "--name-only", base)).splitlines()
    assert ABSENT_MODULE not in at_base and PRECEDENT_FILE in at_base
    # And the answer is grounded the same way round as the tree: it builds on
    # the module that is there, says the other one is not, and its evidence
    # names the module that is there.  Same two constants as the two lines
    # above, so the board text, the tree and the answer cannot drift apart.
    assert PRECEDENT_FILE in record.resolution
    assert f"no {ABSENT_MODULE}" in record.resolution
    assert record.repo_evidence == (REGROUND_EVIDENCE,)
    assert PRECEDENT_FILE in REGROUND_EVIDENCE
    # (a) Not closed.  No state moved and no description was written; the
    # subject and the criterion are still open by kind.
    assert port.issue_writes == []
    assert port.workflow_writes == []
    assert port.restored_states == []
    for key in (SUBJECT, DIRECT_OWED):
        assert port.issues[key].state_kind not in {
            WorkflowStateKind.COMPLETED,
            WorkflowStateKind.CANCELED,
        }
    # (b) Not crossed off.  Read again, the owed set is the set the step was
    # handed, and the criterion still carries the text it had.
    again = await TrackerCriteria(tracker=port).read_current(spec=spec, held=None)
    assert again == current
    assert {criterion.id: criterion.text for criterion in again.criteria}[
        DIRECT_OWED
    ] == PREMISE_CHECK
    # A second pass over the same fixture writes nothing.
    after_first = board_state(port)
    await run(step, spec, current, repo_path, base)
    assert board_state(port) == after_first
