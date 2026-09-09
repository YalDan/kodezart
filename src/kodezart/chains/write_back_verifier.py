"""Prove what a write-back actually put on a surface, and repair it if wrong.

A write that returned without raising has established one thing: the call
went through.  It has not established that the text now on the surface says
anything true — a write-back naming a test that does not exist at the ref it
claims to have run at is exactly as successful, at the call, as a correct
one.  The next consumer reads that text and treats it as evidence.

So the loop is write, RE-READ what landed, judge it, and — where the
judgment refutes it — repair and judge again.  The re-read is not
ceremony: the artifact a consumer will read is the one the backend now
holds, which is not necessarily the string the writing step composed.
"""

from collections.abc import Sequence
from typing import Protocol, Self, runtime_checkable

from pydantic import ConfigDict, Field, model_validator

from kodezart.core.protocols import TrackerPort
from kodezart.services.tracker_artifacts import (
    read_tracker_artifact,
    require_artifact_read,
)
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.surface import WritableSurface


class WriteBackFinding(CamelCaseModel):
    """One round's judgment of the artifact that actually landed.

    ``cited_refs`` are the references the judgment turned on — the test
    paths, files or shas the artifact named and the judgment checked.  A
    refutation must name at least one: a repair round is driven by what
    the previous round found wrong, and a refutation citing nothing
    leaves the writing step guessing at its own defect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: AuditVerdict
    evidence: str = Field(min_length=1, pattern=r"\S")
    cited_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _refutation_names_something(self) -> Self:
        if self.verdict is AuditVerdict.REFUTED and not self.cited_refs:
            raise ValueError("a refuted write-back must cite what it refutes")
        return self


class WriteBackResult(CamelCaseModel):
    """What the loop settled, and the artifact a consumer will now read.

    ``rounds`` is every round's finding in order, so the count of them is
    what the loop spent and the last of them is why it stopped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: AuditVerdict
    artifact: TrackerArtifact
    rounds: tuple[WriteBackFinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _verdict_follows_the_rounds(self) -> Self:
        """The loop settles in two states, and each has to have happened.

        ``refuted`` is not one of them: a refutation is what a repair
        round answers, so the loop either repaired it or ran out of
        rounds with it unsettled, and reporting the artifact itself as
        refuted would hide which of the two occurred.
        """
        holds = self.rounds[-1].verdict is AuditVerdict.HOLDS
        if self.verdict is AuditVerdict.REFUTED:
            raise ValueError("a refuted round is either repaired or left unsettled")
        if (self.verdict is AuditVerdict.HOLDS) is not holds:
            raise ValueError("the result must agree with its own last round")
        return self


@runtime_checkable
class WriteBackStep(Protocol):
    """One writing step, as the verifier drives it.

    The step owns its own write.  It already knows which surface it
    addresses and under which precondition, and a verifier that re-derived
    that would be a second statement of it, free to disagree.
    """

    @property
    def surface(self) -> WritableSurface:
        """The surface this step writes, and the one re-read after it."""
        ...

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        """Put this step's text on its surface.

        ``None`` is the first round.  A repair round carries the previous
        round's finding, so the step repairs what was actually found
        rather than rewriting from scratch and re-introducing it.
        """
        ...


@runtime_checkable
class WriteBackJudge(Protocol):
    """Judge the artifact that landed, in a session that wrote none of it.

    Fresh by construction: a judgment made inside the writing session
    inherits that session's transcript, and a reader that already believes
    the claim is not the reader this loop exists to consult.
    """

    async def judge(self, *, artifact: TrackerArtifact, ref: str) -> WriteBackFinding:
        """Judge *artifact*'s claims against the repository at *ref*."""
        ...


class WriteBackVerifier:
    """Write, re-read, judge, repair, judge again — within a round budget.

    ``max_rounds`` is the operator's, supplied by the caller: how many
    times a deployment is willing to have a writing step try again is a
    deployment's decision and never this loop's.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        judge: WriteBackJudge,
        max_rounds: int,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("a write-back needs at least one round to be judged")
        self._tracker = tracker
        self._judge = judge
        self._max_rounds = max_rounds

    async def write_back(self, *, step: WriteBackStep, ref: str) -> WriteBackResult:
        """Drive *step* until its artifact holds at *ref* or the budget ends.

        Exhaustion is ``unverifiable`` and not ``refuted``: the loop ran
        out of rounds, which says the claim was never settled, not that it
        was disproved — and the last round's refutation stays in
        ``rounds`` for whoever reads why.
        """
        surface = step.surface
        require_artifact_read(surface)
        rounds: list[WriteBackFinding] = []
        finding: WriteBackFinding | None = None
        while True:
            await step.write(finding=finding)
            artifact = await read_tracker_artifact(
                tracker=self._tracker, surface=surface
            )
            finding = await self._judge.judge(artifact=artifact, ref=ref)
            rounds.append(finding)
            if finding.verdict is AuditVerdict.HOLDS:
                return self._result(
                    verdict=AuditVerdict.HOLDS, artifact=artifact, rounds=rounds
                )
            if len(rounds) >= self._max_rounds:
                return self._result(
                    verdict=AuditVerdict.UNVERIFIABLE,
                    artifact=artifact,
                    rounds=rounds,
                )

    @staticmethod
    def _result(
        *,
        verdict: AuditVerdict,
        artifact: TrackerArtifact,
        rounds: Sequence[WriteBackFinding],
    ) -> WriteBackResult:
        return WriteBackResult(verdict=verdict, artifact=artifact, rounds=tuple(rounds))
