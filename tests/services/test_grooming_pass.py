"""The grooming pass's build verification, and AC-41's consumer.

The criterion's subject is *a consumer*: a repository whose chain has a
gate plus dependent steps must round-trip so that someone downstream can
tell which failure is the root and which are cascades. The consumer is
here — the pass classifies the session's raw reds against the declared
chain and writes one finding naming the two sets separately.

The chain under test is deliberately the shape the criterion names: one
gate, two steps depending on it, and one independent step. Three reds
where two are consequences of the first is exactly the report the honesty
rule exists to prevent, so it is the case every assertion below is about.
"""

from collections.abc import Sequence
from pathlib import Path

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import OutboundContentGate, PromptProvider
from kodezart.services.grooming_pass import GroomingPass, report_body
from kodezart.services.pass_session import PassSession
from kodezart.types.domain.gating import OutboundDestination
from kodezart.types.domain.operation import CheckStep, OperationConfig, RepoEntry
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakePassExecutor,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_tracker_issue,
)
from tests.services.test_dispatch_pass import PRIMARY_REPO, SECOND_REPO

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "docs" / "operation.example.toml"

ISSUE = "K-7"
SECOND_ISSUE = "K-8"
HEAD_SHA = "0f1e2d3c4b5a6978"
TRUNK = "trunk"
TIMEOUT_SECONDS = 30.0
TOOLS = ("Bash", "Read")

#: One gate, two steps that only run after it, and one step gated on
#: nothing.  A failure set of {lint, type-check, test} over this chain has
#: exactly one root, and a report that says "three problems" is the defect.
CHAIN: tuple[CheckStep, ...] = (
    CheckStep(name="lint", command="make lint"),
    CheckStep(name="type-check", command="make type-check", depends_on="lint"),
    CheckStep(name="test", command="make test", depends_on="type-check"),
    CheckStep(name="licences", command="make licences"),
)

REPO = RepoEntry(url=PRIMARY_REPO, trunk=TRUNK, checks=CHAIN)


def grooming_config() -> OperationConfig:
    """The shipped annotated example, carrying the chain under test.

    The example is the source rather than a hand-built fixture because the
    grooming template reaches names the smaller dispatch fixture does not
    declare, and a config that cannot render the template would make every
    case below pass by rendering nothing.
    """
    return load_operation_config(EXAMPLE_CONFIG).model_copy(
        update={"repos": [REPO]},
    )


def pass_prompts() -> PromptProvider:
    """The real registry with the operation namespace bound, as boot binds it."""
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set="claude-opus",
        set_overrides={},
        template_overrides={},
        bindings=dict(bindings_for(grooming_config())),
    )


def make_pass(
    *,
    tracker: FakeTrackerPort,
    answers: Sequence[dict[str, object] | None],
    repo: RepoEntry = REPO,
    workspace: FakeWorkspaceProvider | None = None,
    gate: OutboundContentGate | None = None,
    executor: FakePassExecutor | None = None,
) -> GroomingPass:
    return GroomingPass(
        tracker=tracker,
        workspace=workspace or FakeWorkspaceProvider(),
        session=PassSession(
            executor=executor or FakePassExecutor(answers=answers),
            prompts=pass_prompts(),
            skills=SUPPRESS_ALL_SKILLS,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
        gate=gate or PassThroughGate(),
        repo=repo,
        allowed_tools=TOOLS,
    )


def verification(
    *,
    repo_url: str = PRIMARY_REPO,
    failed: Sequence[str] = (),
    issues: Sequence[str] = (ISSUE,),
) -> dict[str, object]:
    return {
        "verifications": [
            {
                "repoUrl": repo_url,
                "headSha": HEAD_SHA,
                "failedSteps": list(failed),
                "issueKeys": list(issues),
            },
        ],
    }


def tracker_with(*keys: str) -> FakeTrackerPort:
    return FakeTrackerPort(issues=[make_tracker_issue(key) for key in keys])


async def test_a_gate_failure_and_its_dependents_are_reported_as_one_root() -> None:
    """AC-41: the consumer names one root and its cascades, not three reds."""
    tracker = tracker_with(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[verification(failed=["lint", "type-check", "test"])],
    ).run()

    (comment,) = tracker.comments
    assert comment.issue_key == ISSUE
    assert "Root failures: lint." in comment.body
    assert "Cascaded from a failed step above them: type-check, test." in comment.body


async def test_two_independent_gates_are_both_roots() -> None:
    """The split is arithmetic over the chain, not "the first red wins"."""
    tracker = tracker_with(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[verification(failed=["lint", "test", "licences"])],
    ).run()

    (comment,) = tracker.comments
    assert "Root failures: lint, licences." in comment.body
    assert "Cascaded from a failed step above them: test." in comment.body


async def test_a_step_the_chain_does_not_declare_is_reported_as_a_root() -> None:
    """A name nobody declared has no ancestor to be a cascade of, and is not lost."""
    tracker = tracker_with(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[verification(failed=["docs"])],
    ).run()

    (comment,) = tracker.comments
    assert "Root failures: docs." in comment.body
    assert "Cascaded from a failed step above them: none." in comment.body


async def test_a_clean_chain_produces_no_comment_at_all() -> None:
    """Grooming that produces no finding produces no comment."""
    tracker = tracker_with(ISSUE)
    gate = PassThroughGate()
    await make_pass(tracker=tracker, answers=[verification()], gate=gate).run()

    assert tracker.comments == []
    assert gate.calls == []


async def test_a_red_chain_blocking_nothing_groomed_writes_no_comment() -> None:
    """The finding is still computed; there is simply nobody it is a reply to."""
    tracker = tracker_with(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[verification(failed=["lint"], issues=())],
    ).run()

    assert tracker.comments == []


async def test_the_finding_reaches_every_issue_the_failure_blocks() -> None:
    """One computation, one gated payload, one comment per blocked item."""
    tracker = tracker_with(ISSUE, SECOND_ISSUE)
    gate = PassThroughGate()
    await make_pass(
        tracker=tracker,
        answers=[verification(failed=["lint"], issues=(ISSUE, SECOND_ISSUE))],
        gate=gate,
    ).run()

    assert [comment.issue_key for comment in tracker.comments] == [
        ISSUE,
        SECOND_ISSUE,
    ]
    assert gate.destinations == [OutboundDestination.TRACKER_COMMENT]


async def test_a_verification_naming_another_repository_is_dropped() -> None:
    """A session standing in one checkout cannot report on a build elsewhere."""
    tracker = tracker_with(ISSUE)
    await make_pass(
        tracker=tracker,
        answers=[verification(repo_url=SECOND_REPO, failed=["lint"])],
    ).run()

    assert tracker.comments == []


async def test_a_session_that_did_not_answer_writes_nothing() -> None:
    """Three states, none silent: no answer is not a green build."""
    tracker = tracker_with(ISSUE)
    await make_pass(tracker=tracker, answers=[None]).run()

    assert tracker.comments == []


async def test_the_checkout_is_released_even_when_the_session_fails() -> None:
    """A workspace held by a failed tick leaks one per interval, forever."""
    workspace = FakeWorkspaceProvider()
    await make_pass(
        tracker=tracker_with(ISSUE),
        answers=[None],
        workspace=workspace,
    ).run()

    assert workspace.calls == [
        ("acquire", PRIMARY_REPO, TRUNK),
        ("release", "/tmp/fake-workspace"),
    ]


async def test_the_session_verifies_inside_the_checkout_with_its_granted_tools() -> (
    None
):
    """A verification session that cannot run the commands is not one."""
    executor = FakePassExecutor(answers=[verification()])
    await make_pass(
        tracker=tracker_with(ISSUE),
        answers=[],
        executor=executor,
    ).run()

    (call,) = executor.calls
    assert call["cwd"] == "/tmp/fake-workspace"
    assert call["allowed_tools"] == list(TOOLS)
    prompt = call["prompt"]
    assert isinstance(prompt, str)
    # The declared chain reaches the session by name and by command, so a
    # session reporting a step name has one it could have read.
    assert "make lint" in prompt
    assert "type-check" in prompt


def test_the_report_names_the_sha_it_was_taken_at() -> None:
    """A red without a sha is a claim about no particular state of the code."""
    from kodezart.domain.check_chain import classify_check_failures

    body = report_body(
        head_sha=HEAD_SHA,
        classification=classify_check_failures(CHAIN, ["lint", "test"]),
    )

    assert HEAD_SHA in body
