"""The grooming pass's build verification: raw reds in, root and cascades out.

One repository per instance, because a verification is performed IN a
checkout and a session can only stand in one.  The session runs that
repository's own declared commands and reports which named steps failed
and at which HEAD sha — observation, nothing else.

The honesty rule is applied HERE and not there.  KOD-60 R8 part 3: the
grooming prompt instructs a session to report a gate failure as a gate
failure and a cascade as a cascade, and an instruction is not a guarantee.
``classify_check_failures`` is a pure function of the chain the operation
config declares — a chain already rejected at load if it names a step
twice, depends on one that is absent, or closes a cycle — and of the set
of failed names.  A session that folds three reds into three independent
problems therefore cannot change what this pass reports.

A clean chain produces no comment.  That is the reply criterion the
routines carry: grooming that produces no finding produces no comment, and
a per-tick "still green" note on every issue is the noise the rule exists
to prevent.

Nothing that costs a checkout or a session runs before the pre-query.  The
tick is gated on the repository's trunk tip (``services/trunk_gate.py``,
KOD-60 R11): at an already-verified tip the classification is the one the
pass already computed, so re-running it would spend a session to re-post
the same finding on the same issues.  The tip is recorded only by a tick
that completed, so a session that did not answer is retried rather than
counted as a verification.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort, WorkspaceProvider
from kodezart.domain.check_chain import (
    CheckFailureClassification,
    classify_check_failures,
)
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.pass_session import PassSession
from kodezart.services.trunk_gate import TrunkGate
from kodezart.types.domain.gating import (
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.passes import (
    GROOMING_SCHEMA,
    GroomingOutput,
    PassSessionFailure,
    RepoVerification,
)
from kodezart.types.domain.prompts import PromptKey


def report_body(
    *,
    head_sha: str,
    classification: CheckFailureClassification,
) -> str:
    """The finding a red chain is worth writing down, and nothing more.

    Roots and cascades are named as two lists rather than one, because the
    whole point of the split is that they are not the same news: a root is
    something to fix and a cascade is something that will go green when the
    root does.  A reader who cannot tell them apart re-derives the chain by
    hand, which is what the classifier exists to stop.
    """
    roots = ", ".join(classification.roots)
    cascades = ", ".join(classification.cascades) or "none"
    return (
        f"Build verification at {head_sha}. "
        f"Root failures: {roots}. "
        f"Cascaded from a failed step above them: {cascades}."
    )


class GroomingPass:
    """One grooming tick over one repository's declared verification chain."""

    def __init__(
        self,
        *,
        pre_query: TrunkGate,
        tracker: TrackerPort,
        workspace: WorkspaceProvider,
        session: PassSession,
        gate: OutboundContentGate,
        repo: RepoEntry,
        allowed_tools: tuple[str, ...],
    ) -> None:
        self._pre_query: TrunkGate = pre_query
        self._tracker: TrackerPort = tracker
        self._workspace: WorkspaceProvider = workspace
        self._session: PassSession = session
        self._gate: OutboundContentGate = gate
        self._repo: RepoEntry = repo
        self._allowed_tools: tuple[str, ...] = allowed_tools
        self._log: BoundLogger = get_logger(__name__)

    async def run(self) -> None:
        """Verify this repository in a checkout, then report what failed and why."""
        tip = await self._pre_query.unverified_tip()
        if tip is None:
            await self._log.ainfo(
                "grooming_pass_skipped_no_new_commit",
                repo_url=self._repo.url,
            )
            return
        answer = await self._verify()
        if isinstance(answer, PassSessionFailure):
            # The tip stays unrecorded: a build nobody performed is not a
            # build that came back green, and the next tick asks again.
            await self._log.awarning(
                "grooming_pass_unanswered",
                repo_url=self._repo.url,
                failure=answer.value,
            )
            return
        for verification in answer.verifications:
            if verification.repo_url != self._repo.url:
                # The template declares every registered repository, so a
                # session standing in one checkout can name another. A
                # verification nobody performed here is dropped rather than
                # attributed to a build that did not run.
                await self._log.awarning(
                    "grooming_verification_out_of_scope",
                    verified_repo_url=self._repo.url,
                    reported_repo_url=verification.repo_url,
                )
                continue
            await self._report(verification)
        self._pre_query.record(tip)

    async def _verify(self) -> GroomingOutput | PassSessionFailure:
        """Run one verification session inside a checkout of this repository."""
        checkout = await self._workspace.acquire(
            repo_url=self._repo.url,
            ref=self._repo.trunk,
            create_branch=False,
        )
        try:
            return await self._session.compose(
                key=PromptKey.GROOMING_PASS,
                variables={},
                schema=GROOMING_SCHEMA,
                model=GroomingOutput,
                cwd=checkout,
                allowed_tools=self._allowed_tools,
            )
        finally:
            await self._workspace.release(checkout)

    async def _report(self, verification: RepoVerification) -> None:
        """Classify the reds and write the finding onto every issue they block."""
        classification = classify_check_failures(
            self._repo.checks,
            verification.failed_steps,
        )
        if not classification.roots and not classification.cascades:
            await self._log.ainfo(
                "grooming_build_verified",
                repo_url=self._repo.url,
                head_sha=verification.head_sha,
            )
            return
        # Emitted whether or not any issue is named. The split is the
        # finding; an operator reading the log can tell root from cascade
        # even for a red chain that blocks nothing groomed this tick.
        await self._log.awarning(
            "grooming_build_failed",
            repo_url=self._repo.url,
            head_sha=verification.head_sha,
            roots=list(classification.roots),
            cascades=list(classification.cascades),
            issue_keys=list(verification.issue_keys),
        )
        if not verification.issue_keys:
            return

        try:
            body = await gated_write(
                gate=self._gate,
                log=self._log,
                content=report_body(
                    head_sha=verification.head_sha,
                    classification=classification,
                ),
                visibility=RepoVisibility.PUBLIC,
                shape=WriterShape.PROSE,
                destination=OutboundDestination.TRACKER_COMMENT,
            )
        except OutboundContentBlockedError as exc:
            await self._log.awarning(
                "grooming_report_blocked",
                repo_url=self._repo.url,
                categories=list(exc.categories),
            )
            return
        for issue_key in verification.issue_keys:
            await self._tracker.post_comment(issue_key=issue_key, body=body)
        await self._log.ainfo(
            "grooming_report_written",
            repo_url=self._repo.url,
            issue_keys=list(verification.issue_keys),
        )
