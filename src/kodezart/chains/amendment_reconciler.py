"""Reconcile a writer's deviation claim against the repository at its base.

UPHELD is the resting state. The writer's claim is an input and never a
finding: every ground is reproduced HERE, by reading the repository at the
resolved base, and a ground the base does not bear out upholds the
criterion as written.

Each ground asks the base a different question — one wants an address the
tree does not carry, one wants a file that IS carried without the quoted
premise in it, one wants an anchor really present and two criteria
demanding different replacements for it, one wants a rule really written
in the repository's own rules document. No single reading answers two of
them, so evidence cannot be carried from the ground it fits to a ground it
does not.

Two failures are deliberately not the same thing. An address the CLAIM
offers that the base cannot read as one exact regular file — a directory,
a symlink, a path the tree does not carry that way — is a claim that
cannot be substantiated, and an unsubstantiated claim is UPHELD. The base
ref is the RUN's, not the claimant's: failing to resolve it is an error
about the run and never a verdict about the criterion.
"""

from collections.abc import Mapping
from typing import assert_never

from kodezart.core.protocols import GitSourceReader, TrackerPort
from kodezart.domain.errors import GitSourceReadError
from kodezart.types.domain.amendment import (
    AmendmentDecision,
    AmendmentVerdict,
    AnyAmendmentClaim,
    MutuallyUnsatisfiable,
    PremiseFalseAtBase,
    RequiresBreakingHouseRule,
    UnsatisfiableAtBase,
    UpheldReason,
)
from kodezart.types.domain.tracker import TrackerIssue


class AmendmentReconciler:
    """The node that decides whether a criterion bends, and on what evidence.

    ``house_rules_path`` is the repository's own rules document, which is
    an operator's choice about a repository and never this module's to
    pick: the reconciler reads the rule from that document at the base
    rather than recognising forbidden constructs by shape.
    """

    def __init__(
        self,
        *,
        source: GitSourceReader,
        tracker: TrackerPort,
        house_rules_path: str,
    ) -> None:
        self._source = source
        self._tracker = tracker
        self._house_rules_path = house_rules_path

    async def reconcile(
        self,
        *,
        claim: AnyAmendmentClaim,
        issue_key: str,
        cwd: str,
        base_ref: str,
    ) -> AmendmentVerdict:
        """Judge one claim; amend only what this reconciler reproduced."""
        criteria = {
            criterion.issue_key: criterion
            for criterion in await self._tracker.read_criteria(issue_key=issue_key)
        }
        subject = criteria.get(claim.subject)
        if subject is None:
            return self._upheld(claim, UpheldReason.SUBJECT_NOT_A_CRITERION)
        base = await self._source.resolve_commit(cwd=cwd, ref=base_ref)
        try:
            reproduced = await self._reproduced(
                claim=claim,
                subject=subject,
                criteria=criteria,
                cwd=cwd,
                base=base,
            )
        except GitSourceReadError:
            return self._upheld(claim, UpheldReason.EVIDENCE_UNREADABLE)
        if not reproduced:
            return self._upheld(claim, UpheldReason.GROUND_NOT_REPRODUCED)
        await self._tracker.edit_description(
            target=subject.issue_key,
            expected=subject.body,
            replacement=claim.amendment,
        )
        return AmendmentVerdict(
            subject=claim.subject,
            decision=AmendmentDecision.AMENDED,
            ground=claim.ground,
        )

    async def _reproduced(
        self,
        *,
        claim: AnyAmendmentClaim,
        subject: TrackerIssue,
        criteria: Mapping[str, TrackerIssue],
        cwd: str,
        base: str,
    ) -> bool:
        match claim:
            case UnsatisfiableAtBase():
                return claim.target_path in subject.body and not await self._carried(
                    cwd=cwd, base=base, path=claim.target_path
                )
            case PremiseFalseAtBase():
                content = await self._content(cwd=cwd, base=base, path=claim.path)
                return (
                    claim.premise in subject.body
                    and content is not None
                    and claim.premise.encode() not in content
                )
            case MutuallyUnsatisfiable():
                return await self._contested(
                    claim=claim,
                    subject=subject,
                    counter=criteria.get(claim.counter_subject),
                    cwd=cwd,
                    base=base,
                )
            case RequiresBreakingHouseRule():
                rules = await self._content(
                    cwd=cwd, base=base, path=self._house_rules_path
                )
                return (
                    rules is not None
                    and claim.rule.encode() in rules
                    and claim.forbidden_construct in claim.rule
                    and claim.forbidden_construct in subject.body
                )
            case _:
                assert_never(claim)

    async def _contested(
        self,
        *,
        claim: MutuallyUnsatisfiable,
        subject: TrackerIssue,
        counter: TrackerIssue | None,
        cwd: str,
        base: str,
    ) -> bool:
        if counter is None:
            return False
        content = await self._content(cwd=cwd, base=base, path=claim.path)
        if content is None:
            return False
        return (
            claim.anchor in subject.body
            and claim.anchor in counter.body
            and claim.subject_demand in subject.body
            and claim.counter_demand in counter.body
            and claim.anchor.encode() in content
            and claim.subject_demand.encode() not in content
            and claim.counter_demand.encode() not in content
        )

    async def _content(self, *, cwd: str, base: str, path: str) -> bytes | None:
        blob = await self._source.find_source(cwd=cwd, commit_sha=base, path=path)
        return None if blob is None else blob.content

    async def _carried(self, *, cwd: str, base: str, path: str) -> bool:
        return await self._content(cwd=cwd, base=base, path=path) is not None

    def _upheld(
        self, claim: AnyAmendmentClaim, reason: UpheldReason
    ) -> AmendmentVerdict:
        return AmendmentVerdict(
            subject=claim.subject,
            decision=AmendmentDecision.UPHELD,
            reason=reason,
        )
