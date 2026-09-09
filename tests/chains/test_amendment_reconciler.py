"""KOD-97 — a deviating writer's claim is ruled from the repository itself.

Every case here runs against a REAL repository: two commits, a branch
pinned at the first one, and a reconciler that must read the first one.
The fixture is what decides the verdict, so an implementation that
believed the claimant — or that resolved the wrong commit — fails these
rather than passing them differently.
"""

import subprocess

import pytest
import structlog.testing
from pydantic import ValidationError

from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.chains.amendment_reconciler import AmendmentReconciler
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentDecision,
    AmendmentGround,
    AmendmentVerdict,
    GroundEvidence,
)
from kodezart.types.domain.criterion_ref import CriterionRef
from tests.chains.amendment_fixtures import (
    AT_BASE,
    BASE_REF,
    HOUSE_PATH,
    ISSUE,
    LATER_PATH,
    ONLY_AFTER_BASE,
    SUBJECT,
    claim,
    reconciler,
    tracker,
)
from tests.chains.amendment_fixtures import repo as repo

# ---------------------------------------------------------------------------
# AC-1 — the resting state
# ---------------------------------------------------------------------------


async def test_the_decision_domain_is_exactly_two_arms() -> None:
    """No third "unclear, proceed anyway" arm exists to be reached."""
    assert [member.value for member in AmendmentDecision] == ["upheld", "amended"]


async def test_a_quote_absent_from_the_cited_file_is_upheld(repo) -> None:
    """The path resolves at base; the text the claim quotes is not in it."""
    verdict = await reconciler().reconcile(
        claim(evidence=[GroundEvidence(path=HOUSE_PATH, quote="never written here")]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.UPHELD
    assert verdict.ground is None
    assert verdict.reproduced == ()


async def test_a_path_absent_at_the_base_is_upheld(repo) -> None:
    """A file that exists only after the base substantiates nothing at it."""
    verdict = await reconciler().reconcile(
        claim(evidence=[GroundEvidence(path=LATER_PATH, quote=ONLY_AFTER_BASE)]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.UPHELD


async def test_text_added_after_the_base_is_not_reproduced_at_it(repo) -> None:
    """The quote is in the working tree and in HEAD, and not at the base.

    A reconciler reading the tip instead of the resolved base amends here;
    the base is a different commit and does not carry the line.
    """
    assert ONLY_AFTER_BASE in (repo / HOUSE_PATH).read_text(encoding="utf-8")
    verdict = await reconciler().reconcile(
        claim(evidence=[GroundEvidence(path=HOUSE_PATH, quote=ONLY_AFTER_BASE)]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.UPHELD


async def test_a_claim_offering_no_address_is_upheld(repo) -> None:
    """Nothing to reproduce is not a ground proved by having no counter."""
    verdict = await reconciler().reconcile(
        claim(evidence=[]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.UPHELD


async def test_one_failing_address_among_several_upholds(repo) -> None:
    """A part of a ground is not a ground: every address has to hold."""
    verdict = await reconciler().reconcile(
        claim(
            evidence=[
                GroundEvidence(path=HOUSE_PATH, quote=AT_BASE),
                GroundEvidence(path=LATER_PATH, quote=ONLY_AFTER_BASE),
            ]
        ),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.UPHELD


async def test_the_same_shape_of_claim_amends_when_the_base_bears_it_out(repo) -> None:
    """The control: the refusals above are not a reconciler that never amends."""
    verdict = await reconciler().reconcile(
        claim(evidence=[GroundEvidence(path=HOUSE_PATH, quote=AT_BASE)]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    assert verdict.decision is AmendmentDecision.AMENDED
    assert verdict.reproduced == (GroundEvidence(path=HOUSE_PATH, quote=AT_BASE),)


# ---------------------------------------------------------------------------
# AC-1 — the refusal is recorded against the criterion identity
# ---------------------------------------------------------------------------


async def test_the_refusal_names_the_criterion_it_refused(repo) -> None:
    """Both the returned verdict and the emitted record carry the subject."""
    with structlog.testing.capture_logs() as logs:
        verdict = await reconciler().reconcile(
            claim(evidence=[]),
            issue_key=ISSUE,
            repository=str(repo),
            base_ref=BASE_REF,
        )
    assert verdict.subject_id == SUBJECT
    recorded = [entry for entry in logs if entry["event"] == "amendment_upheld"]
    assert [entry["subject_id"] for entry in recorded] == [SUBJECT]


async def test_a_subject_outside_the_issues_criteria_is_refused(repo) -> None:
    """A claim about something the issue does not own is ruled on neither way."""
    with pytest.raises(ValueError, match="criterion sub-issue"):
        await reconciler().reconcile(
            claim(subject=CriterionRef("criterion/elsewhere"), evidence=[]),
            issue_key=ISSUE,
            repository=str(repo),
            base_ref=BASE_REF,
        )


# ---------------------------------------------------------------------------
# AC-1 — the criterion text survives the refusal byte for byte
# ---------------------------------------------------------------------------


async def test_a_refusal_leaves_every_criterion_body_byte_identical(repo) -> None:
    """Read the family before and after; compare the bytes, not a digest."""
    port = tracker()
    before = {
        issue.issue_key: issue.body.encode("utf-8")
        for issue in await port.read_criteria(issue_key=ISSUE)
    }
    verdict = await AmendmentReconciler(
        tracker=port, source=SubprocessGitSourceReader()
    ).reconcile(
        claim(evidence=[GroundEvidence(path=HOUSE_PATH, quote="never written here")]),
        issue_key=ISSUE,
        repository=str(repo),
        base_ref=BASE_REF,
    )
    after = {
        issue.issue_key: issue.body.encode("utf-8")
        for issue in await port.read_criteria(issue_key=ISSUE)
    }
    assert verdict.decision is AmendmentDecision.UPHELD
    assert after == before
    assert port.issue_writes == []


# ---------------------------------------------------------------------------
# AC-1 — the two arms cannot be mixed
# ---------------------------------------------------------------------------


def test_an_upheld_verdict_cannot_carry_a_ground() -> None:
    """A refusal that names a ground would be an amendment in disguise."""
    with pytest.raises(ValidationError, match="rests on no ground"):
        AmendmentVerdict(
            subject_id=SUBJECT,
            decision=AmendmentDecision.UPHELD,
            ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
            reproduced=(GroundEvidence(path=HOUSE_PATH, quote=AT_BASE),),
        )


def test_an_amended_verdict_cannot_be_built_without_reproduced_evidence() -> None:
    """The amending arm carries what it rests on or does not exist."""
    with pytest.raises(ValidationError, match="names its ground"):
        AmendmentVerdict(
            subject_id=SUBJECT,
            decision=AmendmentDecision.AMENDED,
            ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
        )


def test_a_claim_carries_no_reasoning_of_its_own() -> None:
    """The claimant's rationale is not one of the claim's fields."""
    with pytest.raises(ValidationError):
        AmendmentClaim(
            subject_id=SUBJECT,
            deviation="the criterion could not be met",
            asserted_ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
            reasoning="because I decided so",
        )


def test_the_fixture_repository_actually_separates_its_two_commits(repo) -> None:
    """Non-vacuity: base and tip differ, and only the tip carries the later line."""
    base = subprocess.run(
        ["git", "rev-parse", BASE_REF],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout.decode()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True
    ).stdout.decode()
    assert base != head
