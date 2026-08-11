"""The stall note reports and does not prescribe (KOD-40/AC-2, AC-3)."""

import ast
import re
from pathlib import Path

from kodezart.domain.stall_report import (
    DO_NOT_MERGE_PREFIX,
    stall_pr_body,
    stall_pr_title,
)
from kodezart.domain.trajectory import fold_trajectory
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from tests.fakes import make_criteria

_CRITERIA = make_criteria("Tests pass", "No lint errors", "Docs updated")
_LANDED = "2" * 40


def _trajectory() -> LoopTrajectory:
    return fold_trajectory(
        [
            IterationRecord(
                iteration=1,
                passed_count=1,
                failing_criterion_ids=[_CRITERIA[1].id, _CRITERIA[2].id],
                commit_sha="1" * 40,
            ),
            IterationRecord(
                iteration=2,
                passed_count=2,
                failing_criterion_ids=[_CRITERIA[1].id],
                commit_sha=_LANDED,
            ),
            IterationRecord(
                iteration=3,
                passed_count=2,
                failing_criterion_ids=[_CRITERIA[1].id],
                commit_sha="3" * 40,
            ),
        ],
        plateau_window=2,
    )


def test_the_title_carries_the_disposition_ahead_of_the_subject() -> None:
    """A reader scanning a list of pull requests sees it without opening one."""
    assert stall_pr_title("Add a thing") == f"{DO_NOT_MERGE_PREFIX} Add a thing"


def test_the_body_states_the_best_score_its_iteration_and_the_landed_commit() -> None:
    """KOD-40/AC-3's three facts, in the prose as well as on the wire."""
    body = stall_pr_body(_trajectory(), _CRITERIA, landed_commit=_LANDED)

    assert "| best pass count | 2 of 3 |" in body
    assert "| best iteration | 2 |" in body
    assert f"| head commit | `{_LANDED}` |" in body
    assert "| iterations run | 3 |" in body


def test_the_body_names_the_criteria_that_passed_in_no_iteration() -> None:
    """Identity AND text, so a reader need not go looking for the criterion."""
    body = stall_pr_body(_trajectory(), _CRITERIA, landed_commit=_LANDED)

    assert f"- {_CRITERIA[1].id}: No lint errors" in body
    assert f"- {_CRITERIA[0].id}:" not in body


def test_the_body_reports_the_pass_count_of_every_iteration() -> None:
    """How the count MOVED, not only where it ended."""
    body = stall_pr_body(_trajectory(), _CRITERIA, landed_commit=_LANDED)

    assert "| 1 | 1 | `" + "1" * 40 + "` |" in body
    assert "| 2 | 2 | `" + _LANDED + "` |" in body
    assert "| 3 | 2 | `" + "3" * 40 + "` |" in body


def test_the_body_says_the_head_is_the_best_iteration_not_the_last() -> None:
    """The one thing a reader would otherwise assume wrongly."""
    body = stall_pr_body(_trajectory(), _CRITERIA, landed_commit=_LANDED)

    assert "BEST iteration, not its last" in body


def test_an_iteration_that_committed_nothing_is_reported_as_such() -> None:
    """A blank commit cell is a fact about the run, not a rendering gap."""
    trajectory = fold_trajectory(
        [
            IterationRecord(
                iteration=1,
                passed_count=2,
                failing_criterion_ids=[_CRITERIA[2].id],
                commit_sha="1" * 40,
            ),
            IterationRecord(
                iteration=2,
                passed_count=1,
                failing_criterion_ids=[_CRITERIA[1].id, _CRITERIA[2].id],
                commit_sha=None,
            ),
        ],
        plateau_window=2,
    )
    body = stall_pr_body(trajectory, _CRITERIA, landed_commit="1" * 40)

    assert "| 2 | 1 | — |" in body


def test_a_run_where_every_criterion_passed_at_some_point_says_so() -> None:
    """The empty never-passed set is stated, never rendered as a blank list."""
    trajectory = fold_trajectory(
        [
            IterationRecord(
                iteration=1,
                passed_count=2,
                failing_criterion_ids=[_CRITERIA[2].id],
                commit_sha="1" * 40,
            ),
            IterationRecord(
                iteration=2,
                passed_count=2,
                failing_criterion_ids=[_CRITERIA[1].id],
                commit_sha="2" * 40,
            ),
        ],
        plateau_window=2,
    )
    body = stall_pr_body(trajectory, _CRITERIA, landed_commit="2" * 40)

    assert "None — every criterion passed in at least one iteration" in body


_PRESCRIPTIVE = re.compile(
    r"\b(should|must|please|try|recommend|suggest|consider|fix|resolve|"
    r"rerun|re-run|next step)\b",
    re.IGNORECASE,
)
_STALL_REPORT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "domain"
    / "stall_report.py"
)


def test_the_note_never_tells_the_reviewer_what_to_do() -> None:
    """KOD-40 §4: reporting facts and prescribing a remedy are different jobs.

    Every literal the module can emit is swept, not only the strings this
    fixture happens to reach — a prescriptive branch nothing here exercises
    would still ship.
    """
    literals = [
        node.value
        for node in ast.walk(ast.parse(_STALL_REPORT.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    body = stall_pr_body(_trajectory(), _CRITERIA, landed_commit=_LANDED)
    offenders = [
        text
        for text in [*literals, body]
        if _PRESCRIPTIVE.search(text) and not text.lstrip().startswith('"""')
    ]
    assert offenders == []


def test_the_note_is_pure_arithmetic_over_data_already_in_hand() -> None:
    """No model call, no I/O: the note cannot disagree with the run."""
    source = _STALL_REPORT.read_text(encoding="utf-8")
    imports = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imports == {
        "collections.abc",
        "kodezart.types.domain.criteria",
        "kodezart.types.domain.trajectory",
    }
