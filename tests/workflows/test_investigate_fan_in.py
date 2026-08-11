"""KOD-89-AC-6 — the workflow script's fan-in guard, tested as code.

The criterion names the assertions and not the runner, and the fire-time
ruling on KOD-89 (FR-4) fixes that gap: the SHIPPED script is executed under
Node by a harness supplying the workflow runtime's own globals, with the
per-dispatch results scripted so a null return is reachable.  Nothing here
re-implements the merge in Python — a re-implementation would prove a copy
correct and leave the artifact untested.

A missing Node interpreter FAILS this module rather than skipping it: the
guard exists to make a silent shortfall loud, and a test that disappears
when a tool is absent is the same silence one layer up.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "kodezart-investigate.js"
HARNESS = Path(__file__).parent / "run_workflow.mjs"

REPO_QUESTIONS = [
    "Which module owns the retry budget?",
    "Where is the criterion id pattern defined?",
]
EXTERNAL_CLAIMS = ["Does the pinned SDK expose a fallback model option?"]
#: The question the scripted null lands on — the second dispatch, so the
#: guard has to preserve position and not merely append a placeholder.
NULL_QUESTION = REPO_QUESTIONS[1]


def answered(question: str) -> dict[str, Any]:
    """One agent's returned evidence for *question*."""
    return {
        "question": question,
        "answered": True,
        "evidence": f"src/kodezart/example.py:1 — answering {question}",
    }


def run_workflow(results: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Execute the shipped script with *results* scripted per dispatch."""
    node = shutil.which("node")
    assert node is not None, (
        "node is required to execute .claude/workflows/kodezart-investigate.js; "
        "this criterion is graded by running the shipped script, not by reading it"
    )
    scenario = {
        "args": {
            "repo_questions": REPO_QUESTIONS,
            "external_claims": EXTERNAL_CLAIMS,
        },
        "results": results,
    }
    completed = subprocess.run(
        [node, str(HARNESS), str(WORKFLOW)],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    parsed: dict[str, Any] = json.loads(completed.stdout)
    return parsed


ALL_ANSWERED: list[dict[str, Any] | None] = [
    answered(REPO_QUESTIONS[0]),
    answered(REPO_QUESTIONS[1]),
    answered(EXTERNAL_CLAIMS[0]),
]
ONE_NULL: list[dict[str, Any] | None] = [
    answered(REPO_QUESTIONS[0]),
    None,
    answered(EXTERNAL_CLAIMS[0]),
]


def test_the_shipped_workflow_file_exists_and_is_the_one_under_test() -> None:
    """Non-vacuity: the harness runs the repository's file, not a fixture."""
    assert WORKFLOW.is_file()
    assert "kodezart-investigate" in WORKFLOW.read_text(encoding="utf-8")


def test_a_null_dispatch_becomes_an_unanswered_finding() -> None:
    """One of three returns nothing: three findings, denominator unmoved."""
    report = run_workflow(ONE_NULL)["report"]

    assert len(report["findings"]) == 3
    assert report["dispatched"] == 3
    assert report["unanswered"] >= 1

    dropped = report["findings"][1]
    assert dropped["answered"] is False
    assert dropped["question"] == NULL_QUESTION


def test_every_dispatch_returning_leaves_nothing_unanswered() -> None:
    """The same three questions, all answered: the counter reads zero."""
    report = run_workflow(ALL_ANSWERED)["report"]

    assert report["dispatched"] == 3
    assert report["unanswered"] == 0
    assert [f["question"] for f in report["findings"]] == [
        *REPO_QUESTIONS,
        *EXTERNAL_CLAIMS,
    ]


def test_each_question_is_dispatched_to_the_lens_its_kind_names() -> None:
    """Repository questions go to the explorer, external claims to the verifier."""
    dispatches = run_workflow(ALL_ANSWERED)["dispatches"]

    assert [d["prompt"] for d in dispatches] == [*REPO_QUESTIONS, *EXTERNAL_CLAIMS]
    assert [d["options"]["agentType"] for d in dispatches] == [
        "explorer",
        "explorer",
        "doc-verifier",
    ]


def test_the_counted_report_is_logged_for_the_dispatching_session() -> None:
    """The count is emitted, so a run without the report still says how it went."""
    executed = run_workflow(ONE_NULL)

    assert "phase:Investigate" in executed["log"]
    assert "2/3 questions answered" in executed["log"]


@pytest.mark.parametrize(
    ("results", "expected"),
    [(ALL_ANSWERED, 0), (ONE_NULL, 1)],
)
def test_unanswered_equals_the_number_of_dispatches_that_returned_nothing(
    results: list[dict[str, Any] | None],
    expected: int,
) -> None:
    """The two scenarios read as one property rather than two anecdotes."""
    assert run_workflow(results)["report"]["unanswered"] == expected
