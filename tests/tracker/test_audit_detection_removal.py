"""Actual Git and tracker sources reach the detector's fresh executor boundary."""

import asyncio
import copy
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.chains.audit_detection_removal import DetectorRemovalVerifier
from kodezart.core.config import AppConfig
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.agent_service import AgentService
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import DETECTOR_REMOVAL_SCHEMA
from kodezart.types.domain.audit import AuditClaimRequest, AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.run_state import LaneRunState
from tests.chains.test_organize import RecordingExecutor
from tests.domain.test_lane_record import record_data
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeRepoCache
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker import test_audit_evidence as fixtures
from tests.tracker.test_audit_claim import CHILD, ROOT, result_event

server = fixtures.server
MECHANISM = "def protected():\n    return 'present'\n"
OTHER = "\ndef unrelated():\n    return 'unrelated'\n"
GUARD = (
    "from mechanism import protected\n\n"
    "def test_guard():\n    assert protected() == 'present'\n"
)
SMOKE = "def test_unrelated():\n    assert 1 + 1 == 2\n"
CHECK = "The protected mechanism remains available."


def command(cwd, *args):
    return subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def execute(cwd, *args):
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
    )


def suite(cwd):
    return execute(cwd, "-m", "pytest", "-q", "-p", "no:cacheprovider")


class NativeProbeExecutor(RecordingExecutor):
    """A fixture judge measures the actual supplied revisions and counterfactual."""

    def __init__(self):
        super().__init__([])
        self.observations = []
        self.damage = None
        self.during = None

    async def stream(self, **kwargs):
        prompt = kwargs["prompt"]
        cwd = Path(kwargs["cwd"])
        prior = re.search(r"<graded_sha>([a-f0-9]{40})</graded_sha>", prompt).group(1)
        head = re.search(r"<head_sha>([a-f0-9]{40})</head_sha>", prompt).group(1)
        assert command(cwd, "rev-parse", "HEAD") == head
        assert command(cwd, "status", "--porcelain") == ""
        before = command(cwd, "show", f"{prior}:mechanism.py") + "\n"
        guard = command(cwd, "show", f"{prior}:test_guard.py") + "\n"
        assert MECHANISM in before and guard == GUARD
        current = (cwd / "mechanism.py").read_text()
        observed = suite(cwd)
        counterfactual = execute(cwd, "-c", guard + "\ntest_guard()\n")
        self.observations.append(
            (prior, head, observed.returncode, counterfactual.returncode)
        )
        lost = (
            MECHANISM not in current
            and observed.returncode == 0
            and counterfactual.returncode != 0
        )
        payload = {
            "criterionKey": CHILD,
            "verdict": "refuted" if lost else "holds",
            "evidence": observed.stdout + observed.stderr + counterfactual.stderr,
            "findings": []
            if not lost
            else [
                {
                    "mechanism": {"path": "mechanism.py", "line": 1, "text": MECHANISM},
                    "detector": {"path": "test_guard.py", "line": 1, "text": GUARD},
                    "absenceDemonstration": (
                        "The retained suite is green; executing the old guard at this "
                        "head fails importing the removed protected function."
                    ),
                }
            ],
        }
        if self.damage:
            self.damage(payload)
        if self.during:
            await self.during()
        self.events = [result_event(subtype="success", structured_output=payload)]
        async for event in super().stream(**kwargs):
            yield event


@pytest.fixture
async def build(tracker, tmp_path):
    remote, author, observer = (
        tmp_path / name for name in ("remote", "author", "observer")
    )
    remote.mkdir()
    author.mkdir()
    command(remote, "init", "--bare", "-q")
    command(author, "init", "-q", "-b", "ordinary-name")
    command(author, "config", "user.name", "Fixture")
    command(author, "config", "user.email", "fixture@example.invalid")
    (author / "mechanism.py").write_text(MECHANISM + OTHER)
    (author / "test_guard.py").write_text(GUARD)
    (author / "test_unrelated.py").write_text(SMOKE)
    command(author, "add", "--all")
    command(author, "commit", "-qm", "Protected mechanism and detecting test")
    baseline = command(author, "rev-parse", "HEAD")
    assert suite(author).returncode == 0
    command(author, "remote", "add", "configured-remote", str(remote))
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    command(
        tmp_path,
        "clone",
        "--bare",
        "-q",
        "-o",
        "configured-remote",
        str(remote),
        str(observer),
    )

    async def make(case="removed", set_name=V5_SET):
        if case != "unchanged":
            (author / "mechanism.py").write_text(OTHER)
        if case in {"removed", "replacement"}:
            (author / "test_guard.py").unlink()
        if case == "replacement":
            (author / "test_replacement.py").write_text(
                GUARD.replace("test_guard", "test_replacement")
            )
        command(author, "add", "--all")
        command(
            author,
            "commit",
            "--allow-empty",
            "-qm",
            "Current mechanism and detector state",
        )
        head = command(author, "rev-parse", "HEAD")
        command(author, "push", "-q", "configured-remote", "ordinary-name")
        body = (
            f"**Check:** {CHECK}\n**Do:** AUTHOR_TRANSCRIPT\n"
            + render_evidence_field(
                CriterionEvidence(graded_sha=baseline, test="OLD_RECORDED_VERDICT")
            )
        )
        await tracker.update_issue(issue_key=CHILD, body=body)
        await tracker.restore_workflow_state(issue_key=CHILD, state_name="Done")
        record = LaneRunState.model_validate(record_data())
        comment = await tracker.post_comment(
            issue_key=ROOT,
            body=render_lane_record(record=record, marker_prefixes=fixtures.PREFIXES),
        )
        request = AuditClaimRequest(
            criterion_key=CHILD,
            lane_issue_key=ROOT,
            lane_key=record.lane_key,
            repo_url=remote.as_uri(),
        )
        git = SubprocessGitService(remote="configured-remote")
        source = SubprocessGitSourceReader()
        cache = FakeRepoCache(repo_path=str(observer))
        workspace = GitWorktreeProvider(
            git=git,
            cache=cache,
        )
        executor = NativeProbeExecutor()
        prompts = load_registry(default_set=set_name)
        runner = AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        )
        sources = AuditSourceReader(
            tracker=tracker,
            records=LaneRecordReader(tracker=tracker, operation=fixtures.OPERATION),
            git=git,
            source=source,
            cache=cache,
            operation=fixtures.OPERATION,
            remote=AppConfig(git={"remote": "configured-remote"}).git.remote,
        )
        sessions = FreshAuditSession(
            git=git,
            workspace=workspace,
            runner=runner,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
        )
        detector = DetectorRemovalVerifier(
            sources=sources, sessions=sessions, git=source, prompts=prompts
        )
        return (
            detector,
            request,
            executor,
            workspace,
            baseline,
            head,
            comment,
            author,
            observer,
        )

    return make


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("case", ["removed", "retained", "replacement", "unchanged"])
async def test_actual_removed_and_retained_detection_are_distinguished(
    build, tracker_writes, case, set_name
):
    (
        detector,
        request,
        executor,
        workspace,
        prior,
        head,
        comment,
        author,
        observer,
    ) = await build(case, set_name)
    before = tracker_writes()
    value = await detector.observe(request)
    assert value.judgment.verdict is (
        AuditVerdict.REFUTED if case == "removed" else AuditVerdict.HOLDS
    )
    assert bool(value.judgment.findings) is (case == "removed")
    assert value.graded_sha == prior and value.head_sha == head
    assert value.record_ref == comment.comment_key and value.check == CHECK
    assert tracker_writes() == before
    assert not workspace._workspaces
    assert command(author, "rev-parse", "HEAD") == head
    assert command(observer, "rev-parse", "ordinary-name") == prior
    args = executor.calls[0]
    assert args["session_id"] is None and args["agents"] == ()
    assert args["output_format"]["schema"] == DETECTOR_REMOVAL_SCHEMA
    assert CHECK in args["prompt"]
    assert "AUTHOR_TRANSCRIPT" not in args["prompt"]
    assert "OLD_RECORDED_VERDICT" not in args["prompt"]
    assert not Path(args["cwd"]).exists()
    assert executor.observations == [
        (
            prior,
            head,
            0 if case in {"removed", "unchanged"} else 2,
            0 if case == "unchanged" else 1,
        )
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "criterion",
        "quote",
        "line",
        "retained",
        "duplicate",
        "missing-evidence",
        "holds-with-findings",
        "unverifiable-with-findings",
        "refuted-without-findings",
    ],
)
async def test_ungrounded_or_malformed_findings_refuse_before_publication(
    build, tracker_writes, damage
):
    detector, request, executor, workspace, *_ = await build()

    def alter(payload):
        if damage == "criterion":
            payload["criterionKey"] = "foreign/check"
        elif damage == "quote":
            payload["findings"][0]["mechanism"]["text"] = "Invented source"
        elif damage == "line":
            payload["findings"][0]["mechanism"]["line"] = 2
        elif damage == "retained":
            payload["findings"][0]["mechanism"] = {
                "path": "test_unrelated.py",
                "line": 1,
                "text": SMOKE,
            }
        elif damage == "duplicate":
            payload["findings"].append(copy.deepcopy(payload["findings"][0]))
        elif damage == "missing-evidence":
            del payload["findings"][0]["absenceDemonstration"]
        elif damage == "refuted-without-findings":
            payload["findings"] = []
        else:
            payload["verdict"] = damage.removesuffix("-with-findings")

    executor.damage = alter
    before = tracker_writes()
    with pytest.raises(AuditEvidenceReadError):
        await detector.observe(request)
    assert tracker_writes() == before and not workspace._workspaces


async def test_source_changed_during_judgment_is_not_reported(build, tracker):
    detector, request, executor, workspace, *_ = await build()

    async def change():
        await tracker.update_issue(
            issue_key=CHILD, body="**Check:** A replacement claim."
        )

    executor.during = change
    with pytest.raises(AuditEvidenceReadError, match="criterion changed"):
        await detector.observe(request)
    assert not workspace._workspaces


async def test_cancellation_during_actual_executor_releases_its_native_workspace(build):
    detector, request, executor, workspace, *_ = await build()
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Event().wait()

    executor.during = wait
    task = asyncio.create_task(detector.observe(request))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert not workspace._workspaces
