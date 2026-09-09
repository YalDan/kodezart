"""A real two-commit repository and a criterion family, shared by the suites.

The repository is the point: the reconciler's verdict has to come from
what is committed at the pinned base, so the fixture commits one line
before that base and one after it, and every case addresses one of them.
"""

import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.chains.amendment_reconciler import AmendmentReconciler
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentGround,
    GroundEvidence,
)
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeTrackerPort

HOUSE_PATH = "src/house.py"
LATER_PATH = "src/later.py"
RULES_PATH = "docs/house_rules.md"
HOUSE_RULE = "Never weaken a test to reach a green gate."
AT_BASE = "the base bears this out"
ONLY_AFTER_BASE = "this line arrives after the base"
BASE_REF = "fixture-base"
ISSUE = "issue/1"
SUBJECT = CriterionRef("criterion/a")
SIBLING = CriterionRef("criterion/b")


def git(cwd: Path, *args: str) -> str:
    """Run one Git command in *cwd* and return its trimmed stdout."""
    return (
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Two commits, with ``fixture-base`` pinned at the first of them."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / HOUSE_PATH).write_text(
        f"# {AT_BASE}\n\n\ndef implementation() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (repo / RULES_PATH).write_text(f"# House rules\n\n{HOUSE_RULE}\n", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "fixture base")
    git(repo, "branch", BASE_REF)
    (repo / HOUSE_PATH).write_text(
        f"# {AT_BASE}\n# {ONLY_AFTER_BASE}\n\n\ndef implementation() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )
    (repo / LATER_PATH).write_text(f"# {ONLY_AFTER_BASE}\n", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "fixture tip")
    return repo


def issue(key: str, body: str, **changes: object) -> TrackerIssue:
    """One tracker issue in the vocabulary the port hands its consumers."""
    return TrackerIssue.model_validate(
        {
            "issue_key": key,
            "title": f"Title for {key}",
            "body": body,
            "priority": IssuePriority.NONE,
            "state_name": "Todo",
            "state_kind": WorkflowStateKind.UNSTARTED,
            "queue_states": [],
            "team_key": "engineering",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "url": f"https://tracker.invalid/{key}",
            **changes,
        }
    )


def tracker() -> FakeTrackerPort:
    """An issue with two criterion sub-issues and one unlabelled child."""
    return FakeTrackerPort(
        issues=[
            issue(ISSUE, "The deliverable body."),
            issue(
                SUBJECT,
                "**Check:** the first criterion, verbatim.\n**Class:** hard",
                parent_key=ISSUE,
                issue_labels=["criterion"],
            ),
            issue(
                SIBLING,
                "**Check:** the second criterion, verbatim.\n**Class:** hard",
                parent_key=ISSUE,
                issue_labels=["criterion"],
            ),
            issue("ordinary/child", "Unlabelled child.", parent_key=ISSUE),
        ]
    )


def reconciler(port: FakeTrackerPort | None = None) -> AmendmentReconciler:
    """The reconciler under test, reading repositories through the real port."""
    return AmendmentReconciler(
        tracker=tracker() if port is None else port,
        source=SubprocessGitSourceReader(),
    )


def claim(
    *,
    evidence: Sequence[GroundEvidence],
    subject: CriterionRef = SUBJECT,
    ground: AmendmentGround = AmendmentGround.UNSATISFIABLE_AT_BASE,
    counter_subject: CriterionRef | None = None,
) -> AmendmentClaim:
    """A claim in the shape a deviating writer submits one."""
    return AmendmentClaim(
        subject_id=subject,
        deviation="the criterion could not be met as written",
        asserted_ground=ground,
        asserted_evidence=tuple(evidence),
        counter_subject=counter_subject,
    )
