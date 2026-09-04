"""The shared remediation component (KOD-48/AC-3, AC-4, AC-9)."""

import pytest

from kodezart.chains.remediation import RemediationChain
from kodezart.core.errors import NoStructuredOutputError
from kodezart.core.protocols import Remediator
from kodezart.domain.remediation import NO_TRAJECTORY, done_work_summary
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.agent import (
    ResultEvent,
    WorkflowRemediationEvent,
)
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.trajectory import IterationRecord
from kodezart.types.domain.workflow import RemediationRequest
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    RecordingPromptProvider,
    make_dispatched_criteria,
    make_prompt_provider,
    make_ticket_draft,
)

_ORIGINAL_TITLE = "Add the widget endpoint"
_EVIDENCE = "CI failed: the widget suite errored on an unhandled None."


def _request(
    *,
    entry: RemediationEntry = RemediationEntry.ci_failure,
    round_index: int = 0,
    with_trajectory: bool = True,
    pr_url: str | None = "https://github.com/o/r/pull/3",
) -> RemediationRequest:
    trajectory = (
        fold_trajectory(
            [
                IterationRecord(
                    iteration=1,
                    passed_count=2,
                    failing_criterion_ids=["AC-3"],
                    commit_sha="1" * 40,
                ),
                IterationRecord(
                    iteration=2,
                    passed_count=2,
                    failing_criterion_ids=["AC-3"],
                    commit_sha="2" * 40,
                ),
            ],
            plateau_window=2,
        )
        if with_trajectory
        else None
    )
    return RemediationRequest(
        entry=entry,
        round_index=round_index,
        original_ticket=make_ticket_draft(title=_ORIGINAL_TITLE),
        work_branch="kodezart/widget-12345678",
        work_base_ref="kodezart/widget-12345678",
        pr_url=pr_url,
        total_iterations=2,
        trajectory=trajectory,
        criteria=make_dispatched_criteria(),
        failure_evidence=_EVIDENCE,
    )


def _ticket_result() -> ResultEvent:
    return ResultEvent(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="remediation",
        structured_output=make_ticket_draft(
            title="Handle the None the widget suite hit",
        ).model_dump(by_alias=True),
    )


def _chain(
    runner: FakeAgentRunner,
    prompts: RecordingPromptProvider | None = None,
) -> RemediationChain:
    return RemediationChain(
        service=runner,
        prompts=prompts if prompts is not None else make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
    )


def test_the_chain_satisfies_the_port() -> None:
    """Callers depend on the protocol, so both entries reach one type."""
    assert isinstance(_chain(FakeAgentRunner(events=[])), Remediator)


async def test_the_round_yields_the_drafted_ticket_with_its_entry() -> None:
    """AC-6: the round's ticket is observable, and says which route fired."""
    runner = FakeAgentRunner(events=[_ticket_result()])

    events = [
        e
        async for e in _chain(runner).run(
            _request(entry=RemediationEntry.loop_not_accepted, round_index=1),
            repo_path="/tmp/fake",
            repo_url=None,
            cache_key="job-1",
        )
    ]

    remediation = next(e for e in events if isinstance(e, WorkflowRemediationEvent))
    assert remediation.entry is RemediationEntry.loop_not_accepted
    assert remediation.round_index == 1
    assert remediation.ticket.title == "Handle the None the widget suite hit"
    assert remediation.base_ref == "kodezart/widget-12345678"


async def test_the_session_holds_all_three_parts_of_its_contract() -> None:
    """AC-9: original ticket, done-work summary, and the entry's evidence."""
    runner = FakeAgentRunner(events=[_ticket_result()])

    _ = [
        e
        async for e in _chain(runner).run(
            _request(),
            repo_path="/tmp/fake",
            repo_url=None,
            cache_key="job-1",
        )
    ]

    prompt = str(runner.calls[0]["prompt"])
    assert _ORIGINAL_TITLE in prompt
    assert _EVIDENCE in prompt
    assert "kodezart/widget-12345678" in prompt
    assert "https://github.com/o/r/pull/3" in prompt
    assert "AC-3" in prompt


async def test_the_session_is_fresh_and_carries_no_prior_conversation() -> None:
    """AC-3: the round re-derives from evidence, it does not continue a chat."""
    runner = FakeAgentRunner(events=[_ticket_result()])

    _ = [
        e
        async for e in _chain(runner).run(
            _request(),
            repo_path="/tmp/fake",
            repo_url=None,
            cache_key="job-1",
        )
    ]

    assert len(runner.calls) == 1
    assert runner.calls[0]["method"] == "stream"


async def test_a_session_with_no_structured_output_names_its_raise_site() -> None:
    """A silent empty round would be indistinguishable from a ticket-less run."""
    runner = FakeAgentRunner(events=[])

    with pytest.raises(NoStructuredOutputError) as excinfo:
        _ = [
            e
            async for e in _chain(runner).run(
                _request(),
                repo_path="/tmp/fake",
                repo_url=None,
                cache_key="job-1",
            )
        ]

    assert excinfo.value.raise_site == "remediation_ticket"


def test_the_done_work_summary_reports_the_trajectory_it_was_given() -> None:
    """What exists, never what to do about it — the remedy is the session's job."""
    summary = done_work_summary(_request())

    assert "kodezart/widget-12345678" in summary
    assert "Iterations spent so far: 2" in summary
    assert "Passed in no iteration: AC-3" in summary
    assert "Best pass count: 2 at iteration 1" in summary


def test_a_run_with_no_recorded_iterations_says_so() -> None:
    """The empty trajectory is stated, not rendered as an empty table."""
    summary = done_work_summary(_request(with_trajectory=False, pr_url=None))

    assert NO_TRAJECTORY in summary
    assert "| iteration | passed | still failing |" not in summary
