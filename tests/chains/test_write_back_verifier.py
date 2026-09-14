"""The write-back loop: refute what landed, repair it, and prove the repair.

Every double here is DRIVEN by the artifact rather than by a script.  The
judge re-derives its verdict from the paths the text actually names and the
paths that actually exist at the ref it was handed, and the writing step
repairs exactly what the previous round cited.  A canned refuted-then-holds
pair would pass against a loop that never re-read, never repaired and never
passed the caller's ref on — which is the whole of what this criterion is
about.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kodezart.chains.write_back_verifier import (
    WriteBackFinding,
    WriteBackVerifier,
)
from kodezart.core.protocols import TrackerPort
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeTrackerPort
from tests.tracker.lease_fixtures import leased_comment

ISSUE = "FIX-77"
MARKER = "[fixture-evidence:lane-alpha]"
SURFACE = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE),
    marker=MARKER,
)

#: The two refs the fixture repository has, and the test modules that exist
#: at each.  Two of them, because a loop that judged at a ref of its own
#: choosing rather than the caller's would be invisible against one.
HEAD = "5f2c1ab"
OLDER = "1a90dd4"
#: The module the write-back should have named — present at head, and not
#: yet written at the older ref.
REAL_PATH = "tests/chains/test_write_back_verifier.py"
#: The module the first output names, which exists at neither ref.
ABSENT_PATH = "tests/chains/test_write_back_verifer.py"
PATHS_AT_REF: Mapping[str, frozenset[str]] = {
    HEAD: frozenset({REAL_PATH, "tests/chains/test_audit_pass.py"}),
    OLDER: frozenset({"tests/chains/test_audit_pass.py"}),
}

FIRST_OUTPUT = f"086e42b — {ABSENT_PATH}::TestWriteBack passes; gate 9203/25"

_TEST_PATH = re.compile(r"tests/[\w/]+\.py")


def _named_paths(content: str) -> tuple[str, ...]:
    """Every test module the artifact names, first mention first."""
    return tuple(dict.fromkeys(_TEST_PATH.findall(content)))


class PathCheckingJudge:
    """Stands in for the fresh session, and judges rather than recites.

    It reads the artifact it is handed, takes every test module that text
    names, and checks each one against the modules that exist at the ref it
    was handed.  Nothing about the round it is in reaches it.
    """

    def __init__(self, *, paths_at_ref: Mapping[str, frozenset[str]]) -> None:
        self._paths_at_ref = paths_at_ref
        self.seen: list[tuple[TrackerArtifact, str]] = []

    async def judge(self, *, artifact: TrackerArtifact, ref: str) -> WriteBackFinding:
        self.seen.append((artifact, ref))
        named = _named_paths(artifact.content)
        absent = tuple(path for path in named if path not in self._paths_at_ref[ref])
        if absent:
            return WriteBackFinding(
                verdict=AuditVerdict.REFUTED,
                evidence=f"named at {ref} but not present there",
                cited_refs=absent,
            )
        return WriteBackFinding(
            verdict=AuditVerdict.HOLDS,
            evidence=f"every named module exists at {ref}",
            cited_refs=named,
        )


class EvidenceWriteBack:
    """A real writing step: it upserts its own marker comment through the port.

    Its repair is driven by the finding it is handed — it replaces exactly
    the references the previous round cited with the module it actually
    ran — so a loop that dropped the finding leaves the defect in place.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        first_output: str,
        correction: str,
    ) -> None:
        self._tracker = tracker
        self._first_output = first_output
        self._correction = correction
        self._latest = first_output
        self.findings: list[WriteBackFinding | None] = []

    @property
    def surface(self) -> WritableSurface:
        return SURFACE

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        self.findings.append(finding)
        if finding is not None:
            for cited in finding.cited_refs:
                self._latest = self._latest.replace(cited, self._correction)
        await leased_comment(
            self._tracker, target=ISSUE, marker=MARKER, body=self._latest
        )


class StubbornWriteBack(EvidenceWriteBack):
    """A step whose repair round changes nothing it was told was wrong."""

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        self.findings.append(finding)
        await leased_comment(
            self._tracker, target=ISSUE, marker=MARKER, body=self._first_output
        )


def tracker() -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[
            TrackerIssue.model_validate(
                {
                    "issue_key": ISSUE,
                    "title": "a criterion carrying evidence",
                    "body": "Check / Do / Evidence",
                    "priority": IssuePriority.NONE,
                    "state_name": "Todo",
                    "state_kind": WorkflowStateKind.UNSTARTED,
                    "queue_states": [],
                    "team_key": "engineering",
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "url": f"https://tracker.invalid/{ISSUE}",
                },
            ),
        ],
        marker_prefixes={"evidence": "fixture-evidence"},
    )


def verifier(
    port: TrackerPort, judge: PathCheckingJudge, *, max_rounds: int
) -> WriteBackVerifier:
    return WriteBackVerifier(tracker=port, judge=judge, max_rounds=max_rounds)


class TestTheRepairRound:
    """Round one refutes, the repair round rewrites, round two holds."""

    async def test_a_bad_write_back_is_refuted_then_repaired(self) -> None:
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        result = await verifier(port, judge, max_rounds=3).write_back(
            step=step, ref=HEAD
        )

        assert [item.verdict for item in result.rounds] == [
            AuditVerdict.REFUTED,
            AuditVerdict.HOLDS,
        ]
        assert result.rounds[0].cited_refs == (ABSENT_PATH,)
        assert result.verdict is AuditVerdict.HOLDS

    async def test_the_repair_round_is_handed_what_the_first_round_found(
        self,
    ) -> None:
        """Round one writes with nothing; the repair carries that finding."""
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        result = await verifier(port, judge, max_rounds=3).write_back(
            step=step, ref=HEAD
        )

        assert step.findings == [None, result.rounds[0]]

    async def test_the_artifact_is_re_read_between_every_round(self) -> None:
        """Two writes, two reads, and each read is of what then stood."""
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        await verifier(port, judge, max_rounds=3).write_back(step=step, ref=HEAD)

        assert [_named_paths(artifact.content) for artifact, _ in judge.seen] == [
            (ABSENT_PATH,),
            (REAL_PATH,),
        ]

    async def test_the_judge_reads_what_landed_not_what_the_step_composed(
        self,
    ) -> None:
        """The port stores the marker line the step never wrote itself."""
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        await verifier(port, judge, max_rounds=3).write_back(step=step, ref=HEAD)

        first, _ = judge.seen[0]
        assert first.content == f"{MARKER}\n{FIRST_OUTPUT}"
        assert first.content != FIRST_OUTPUT
        assert first.native_ref == port.comments[0].comment_key


class TestTheConsumerReadsTheRepair:
    """The defective text never reaches the next reader of the surface."""

    async def test_the_next_consumer_reads_the_repaired_artifact(self) -> None:
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )
        result = await verifier(port, judge, max_rounds=3).write_back(
            step=step, ref=HEAD
        )

        consumed = await read_tracker_artifact(tracker=port, surface=SURFACE)

        assert consumed.content == result.artifact.content
        assert REAL_PATH in consumed.content
        assert ABSENT_PATH not in consumed.content

    async def test_the_repair_replaced_the_defect_rather_than_joining_it(
        self,
    ) -> None:
        """One surface, one comment: the defective text is gone, not below."""
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        await verifier(port, judge, max_rounds=3).write_back(step=step, ref=HEAD)

        assert [comment.body for comment in port.comments] == [
            f"{MARKER}\n{FIRST_OUTPUT.replace(ABSENT_PATH, REAL_PATH)}",
        ]


class TestTheRefTheCallerNames:
    """The artifact is judged at the caller's ref, never at one of its own."""

    async def test_a_path_absent_at_the_named_ref_is_refuted_there(self) -> None:
        """The same corrected module is not present at the older ref."""
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        result = await verifier(port, judge, max_rounds=2).write_back(
            step=step, ref=OLDER
        )

        assert [ref for _, ref in judge.seen] == [OLDER, OLDER]
        assert result.verdict is AuditVerdict.UNVERIFIABLE
        assert result.rounds[-1].cited_refs == (REAL_PATH,)

    async def test_a_write_back_that_already_holds_takes_one_round(self) -> None:
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = EvidenceWriteBack(
            tracker=port,
            first_output=f"086e42b — {REAL_PATH}::TestWriteBack passes",
            correction=REAL_PATH,
        )

        result = await verifier(port, judge, max_rounds=3).write_back(
            step=step, ref=HEAD
        )

        assert result.verdict is AuditVerdict.HOLDS
        assert step.findings == [None]


class TestTheRoundBudget:
    """A repair that never repairs stops, and says the claim never settled."""

    @pytest.mark.parametrize("budget", [1, 2, 4])
    async def test_an_unrepaired_write_back_stops_at_its_budget(
        self, budget: int
    ) -> None:
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
        step = StubbornWriteBack(
            tracker=port, first_output=FIRST_OUTPUT, correction=REAL_PATH
        )

        result = await verifier(port, judge, max_rounds=budget).write_back(
            step=step, ref=HEAD
        )

        assert result.verdict is AuditVerdict.UNVERIFIABLE
        assert len(result.rounds) == budget
        assert len(step.findings) == budget

    async def test_a_loop_with_no_round_to_spend_is_refused(self) -> None:
        port = tracker()
        judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)

        with pytest.raises(ValueError, match="at least one round"):
            verifier(port, judge, max_rounds=0)


class TestAFindingNamesWhatItRefutes:
    """A refutation nothing can be repaired from is not constructible."""

    def test_a_refutation_citing_nothing_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="cite what it refutes"):
            WriteBackFinding(
                verdict=AuditVerdict.REFUTED, evidence="something is wrong"
            )

    def test_a_holding_finding_needs_no_citation(self) -> None:
        finding = WriteBackFinding(
            verdict=AuditVerdict.HOLDS, evidence="nothing to cite"
        )
        assert finding.cited_refs == ()
