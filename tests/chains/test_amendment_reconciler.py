"""The reconciler reproduces its own evidence, or the criterion stands.

Every case here runs against a real two-commit repository. The base is the
FIRST commit, and the second commit moves every fact the four grounds read,
so "the repository says so" and "the repository says so now" are different
answers and a fixture cannot pass by reading whatever is checked out.
"""

import subprocess
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.chains.amendment_reconciler import AmendmentReconciler
from kodezart.domain.errors import GitSourceReadError
from kodezart.types.domain.amendment import (
    AmendmentDecision,
    AmendmentGround,
    AmendmentVerdict,
    MutuallyUnsatisfiable,
    PremiseFalseAtBase,
    RequiresBreakingHouseRule,
    UnsatisfiableAtBase,
    UpheldReason,
)
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)
from tests.fakes import FakeTrackerPort

PARENT = "fire/7"
BASE = "fixture-base"
HOUSE = "src/house.py"
CONTESTED = "src/contested.py"
RULES = "docs/house_rules.md"
LATER = "src/added_later.py"
SYMLINK = "src/linked.py"
DIRECTORY = "src"

BASE_LINE = "the base carries this line"
LATER_LINE = 'return "a later addition"'
ANCHOR = "RETRY_LIMIT = 3"
LATER_ANCHOR = "LATER_ANCHOR = 1"
RULE = "Never add a fallback for a value an operator chooses."
LATER_RULE = "Never disable the linter."
UNWRITTEN = "a line no commit of this fixture ever carried"

#: Both addresses and a quote no commit carries, so each of the matrix's
#: four claims is equally well asserted by whoever makes it.
BOTH_ADDRESSES = f"Check: `{HOUSE}` and `{LATER}` both carry `{UNWRITTEN}`."

BODIES = {
    "AC-1": f"Check: `{LATER}` exports one canonical reader.",
    "AC-2": f"Check: `{HOUSE}` exports one canonical reader.",
    "AC-3": f"Check: the base already carries `{LATER_LINE}` in `{HOUSE}`.",
    "AC-4": f"Check: the base already carries `{BASE_LINE}` in `{HOUSE}`.",
    "AC-5": f"Check: `{ANCHOR}` in `{CONTESTED}` becomes `RETRY_LIMIT = 5`.",
    "AC-6": f"Check: `{ANCHOR}` in `{CONTESTED}` becomes `RETRY_LIMIT = 9`.",
    "AC-7": f"Check: `{LATER_ANCHOR}` in `{CONTESTED}` becomes `LATER_ANCHOR = 5`.",
    "AC-8": f"Check: `{LATER_ANCHOR}` in `{CONTESTED}` becomes `LATER_ANCHOR = 9`.",
    "AC-9": "Check: the reader must add a fallback when the source read fails.",
    "AC-10": "Check: the sweep must disable the linter for the generated module.",
    "AC-11": BOTH_ADDRESSES,
    "AC-12": BOTH_ADDRESSES,
    "AC-13": BOTH_ADDRESSES,
    "AC-14": BOTH_ADDRESSES,
}


def git(cwd, *args):
    return (
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def write(repo, path, body):
    (repo / path).write_text(body, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """A repository whose base commit is genuinely older than its head.

    The second commit adds a file, adds a line to a carried file, replaces
    the contested anchor and rewrites the rules document — one movement per
    ground, so a reconciler reading the head instead of the base gets the
    opposite answer to every one of them.
    """
    repo = tmp_path / "repository"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    write(repo, HOUSE, f'CARRIED = "{BASE_LINE}"\n')
    write(repo, CONTESTED, f"{ANCHOR}\n")
    write(repo, RULES, f"{RULE}\n")
    (repo / SYMLINK).symlink_to("house.py")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "base")
    git(repo, "branch", BASE)
    write(repo, LATER, "LATER = 1\n")
    write(repo, HOUSE, f'CARRIED = "{BASE_LINE}"\n\ndef later():\n    {LATER_LINE}\n')
    write(repo, CONTESTED, f"RETRY_LIMIT = 5\n{LATER_ANCHOR}\n")
    write(repo, RULES, f"{LATER_RULE}\n")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "after the base")
    return repo


def issue(key, body, **changes):
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


def criterion(key, body):
    return issue(key, body, parent_key=PARENT, issue_labels=frozenset({"criterion"}))


@pytest.fixture
def tracker():
    return FakeTrackerPort(
        issues=[
            issue(PARENT, "The fire this criterion family belongs to."),
            *(criterion(key, body) for key, body in BODIES.items()),
        ]
    )


@pytest.fixture
def reconciler(tracker):
    return AmendmentReconciler(
        source=SubprocessGitSourceReader(),
        tracker=tracker,
        house_rules_path=RULES,
    )


def amended(subject, ground):
    return AmendmentVerdict(
        subject=subject, decision=AmendmentDecision.AMENDED, ground=ground
    )


def upheld(subject, reason=UpheldReason.GROUND_NOT_REPRODUCED):
    return AmendmentVerdict(
        subject=subject, decision=AmendmentDecision.UPHELD, reason=reason
    )


AMEND = "Amended: the reconciler proved this one."

#: One amending case and one paired negative per ground. A negative is the
#: SAME ground asserted over a fixture that does not bear it out, so what
#: separates the pair is the repository and never the label.
CASES = [
    (
        "unsatisfiable-reproduced",
        UnsatisfiableAtBase(subject="AC-1", amendment=AMEND, target_path=LATER),
        amended("AC-1", AmendmentGround.UNSATISFIABLE_AT_BASE),
    ),
    (
        "unsatisfiable-unsupported",
        UnsatisfiableAtBase(subject="AC-2", amendment=AMEND, target_path=HOUSE),
        upheld("AC-2"),
    ),
    (
        "premise-false-reproduced",
        PremiseFalseAtBase(
            subject="AC-3", amendment=AMEND, path=HOUSE, premise=LATER_LINE
        ),
        amended("AC-3", AmendmentGround.PREMISE_FALSE_AT_BASE),
    ),
    (
        "premise-false-unsupported",
        PremiseFalseAtBase(
            subject="AC-4", amendment=AMEND, path=HOUSE, premise=BASE_LINE
        ),
        upheld("AC-4"),
    ),
    (
        "mutual-reproduced",
        MutuallyUnsatisfiable(
            subject="AC-5",
            amendment=AMEND,
            counter_subject="AC-6",
            path=CONTESTED,
            anchor=ANCHOR,
            subject_demand="RETRY_LIMIT = 5",
            counter_demand="RETRY_LIMIT = 9",
        ),
        amended("AC-5", AmendmentGround.MUTUALLY_UNSATISFIABLE),
    ),
    (
        "mutual-unsupported",
        MutuallyUnsatisfiable(
            subject="AC-7",
            amendment=AMEND,
            counter_subject="AC-8",
            path=CONTESTED,
            anchor=LATER_ANCHOR,
            subject_demand="LATER_ANCHOR = 5",
            counter_demand="LATER_ANCHOR = 9",
        ),
        upheld("AC-7"),
    ),
    (
        "house-rule-reproduced",
        RequiresBreakingHouseRule(
            subject="AC-9",
            amendment=AMEND,
            rule=RULE,
            forbidden_construct="add a fallback",
        ),
        amended("AC-9", AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE),
    ),
    (
        "house-rule-unsupported",
        RequiresBreakingHouseRule(
            subject="AC-10",
            amendment=AMEND,
            rule=LATER_RULE,
            forbidden_construct="disable the linter",
        ),
        upheld("AC-10"),
    ),
]

MATRIX = [
    (
        "premise-against-a-carried-file",
        PremiseFalseAtBase(
            subject="AC-11", amendment=AMEND, path=HOUSE, premise=UNWRITTEN
        ),
        AmendmentDecision.AMENDED,
    ),
    (
        "premise-against-a-file-the-base-lacks",
        PremiseFalseAtBase(
            subject="AC-12", amendment=AMEND, path=LATER, premise=UNWRITTEN
        ),
        AmendmentDecision.UPHELD,
    ),
    (
        "unsatisfiable-against-a-carried-file",
        UnsatisfiableAtBase(subject="AC-13", amendment=AMEND, target_path=HOUSE),
        AmendmentDecision.UPHELD,
    ),
    (
        "unsatisfiable-against-a-file-the-base-lacks",
        UnsatisfiableAtBase(subject="AC-14", amendment=AMEND, target_path=LATER),
        AmendmentDecision.AMENDED,
    ),
]


@pytest.mark.parametrize(
    ("claim", "expected"),
    [(claim, expected) for _, claim, expected in CASES],
    ids=[case for case, _, _ in CASES],
)
async def test_each_ground_amends_only_where_the_base_reproduces_it(
    repo, reconciler, claim, expected
):
    verdict = await reconciler.reconcile(
        claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
    )
    assert verdict == expected


def test_the_eight_fixtures_pair_every_ground_both_ways():
    """The Check asks for one case and one paired negative per ground."""
    pairs: dict[AmendmentGround, list[AmendmentDecision]] = {}
    for _, claim, expected in CASES:
        pairs.setdefault(claim.ground, []).append(expected.decision)
    assert set(pairs) == set(AmendmentGround)
    assert all(
        sorted(decisions) == [AmendmentDecision.AMENDED, AmendmentDecision.UPHELD]
        for decisions in pairs.values()
    )
    assert len(CASES) == 2 * len(AmendmentGround)


def test_the_ground_vocabulary_is_exactly_the_four_and_no_fifth():
    assert [ground.value for ground in AmendmentGround] == [
        "unsatisfiable_at_base",
        "mutually_unsatisfiable",
        "premise_false_at_base",
        "requires_breaking_house_rule",
    ]


def test_every_ground_declares_its_own_evidence_and_no_two_share_a_shape():
    """A ground's evidence belongs to its model, so relabelling carries none."""
    declared = {
        claim_type.model_fields["ground"].default: frozenset(claim_type.model_fields)
        - {"subject", "amendment", "ground"}
        for claim_type in (
            UnsatisfiableAtBase,
            PremiseFalseAtBase,
            MutuallyUnsatisfiable,
            RequiresBreakingHouseRule,
        )
    }
    assert declared == {
        AmendmentGround.UNSATISFIABLE_AT_BASE: frozenset({"target_path"}),
        AmendmentGround.PREMISE_FALSE_AT_BASE: frozenset({"path", "premise"}),
        AmendmentGround.MUTUALLY_UNSATISFIABLE: frozenset(
            {"counter_subject", "path", "anchor", "subject_demand", "counter_demand"}
        ),
        AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE: frozenset(
            {"rule", "forbidden_construct"}
        ),
    }
    assert len(set(declared.values())) == len(AmendmentGround)


@pytest.mark.parametrize(
    ("claim", "decision"),
    [(claim, decision) for _, claim, decision in MATRIX],
    ids=[case for case, _, _ in MATRIX],
)
async def test_the_repository_and_not_the_label_decides_the_arm(
    repo, reconciler, claim, decision
):
    """One label over two repository facts, and one fact under two labels.

    All four subjects carry the same criterion text, naming both addresses
    and the quote, so nothing about the claimant separates these four. The
    arm still moves with the base — and it moves in opposite directions for
    the two labels, which neither a reading that ignored the repository nor
    one that let the label stand in for it could produce.
    """
    verdict = await reconciler.reconcile(
        claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
    )
    assert verdict.decision is decision


async def test_the_reconciler_does_not_shop_for_a_ground_that_would_fit(
    repo, reconciler
):
    """The fact that reproduces one ground does not reproduce another.

    ``AC-1`` names a path the base does not carry, which is exactly what
    ``UNSATISFIABLE_AT_BASE`` wants and exactly what ``PREMISE_FALSE_AT_BASE``
    cannot use, since a premise is contradicted only by a file that is
    there. ``AC-9`` is a real house-rule breach that is no other ground.
    Each refused claim is judged before the one that amends its subject.
    """
    mislabelled = [
        await reconciler.reconcile(
            claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
        )
        for claim in (
            PremiseFalseAtBase(
                subject="AC-1", amendment=AMEND, path=LATER, premise=LATER
            ),
            UnsatisfiableAtBase(subject="AC-9", amendment=AMEND, target_path=HOUSE),
        )
    ]
    proved = [
        await reconciler.reconcile(
            claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
        )
        for claim in (
            UnsatisfiableAtBase(subject="AC-1", amendment=AMEND, target_path=LATER),
            RequiresBreakingHouseRule(
                subject="AC-9",
                amendment=AMEND,
                rule=RULE,
                forbidden_construct="add a fallback",
            ),
        )
    ]
    assert mislabelled == [upheld("AC-1"), upheld("AC-9")]
    assert proved == [
        amended("AC-1", AmendmentGround.UNSATISFIABLE_AT_BASE),
        amended("AC-9", AmendmentGround.REQUIRES_BREAKING_HOUSE_RULE),
    ]


async def test_a_house_rule_the_criterion_does_not_demand_is_not_a_breach(
    repo, reconciler
):
    """The rule is really in the document; this criterion never asks for it."""
    verdict = await reconciler.reconcile(
        claim=RequiresBreakingHouseRule(
            subject="AC-2",
            amendment=AMEND,
            rule=RULE,
            forbidden_construct="add a fallback",
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-2")


async def test_a_rule_that_does_not_name_the_construct_is_not_a_breach(
    repo, reconciler
):
    verdict = await reconciler.reconcile(
        claim=RequiresBreakingHouseRule(
            subject="AC-9",
            amendment=AMEND,
            rule=RULE,
            forbidden_construct="the source read fails",
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-9")


async def test_a_mutual_claim_whose_counter_criterion_is_absent_is_upheld(
    repo, reconciler
):
    verdict = await reconciler.reconcile(
        claim=MutuallyUnsatisfiable(
            subject="AC-5",
            amendment=AMEND,
            counter_subject="AC-404",
            path=CONTESTED,
            anchor=ANCHOR,
            subject_demand="RETRY_LIMIT = 5",
            counter_demand="RETRY_LIMIT = 9",
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-5")


async def test_a_mutual_claim_whose_counter_never_made_the_demand_is_upheld(
    repo, reconciler
):
    """AC-4 is a real criterion that says nothing about the contested anchor."""
    verdict = await reconciler.reconcile(
        claim=MutuallyUnsatisfiable(
            subject="AC-5",
            amendment=AMEND,
            counter_subject="AC-4",
            path=CONTESTED,
            anchor=ANCHOR,
            subject_demand="RETRY_LIMIT = 5",
            counter_demand="RETRY_LIMIT = 9",
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-5")


def test_a_mutual_claim_has_to_name_another_criterion_and_a_second_demand():
    with pytest.raises(ValidationError, match="not mutually unsatisfiable with itself"):
        MutuallyUnsatisfiable(
            subject="AC-5",
            amendment=AMEND,
            counter_subject="AC-5",
            path=CONTESTED,
            anchor=ANCHOR,
            subject_demand="RETRY_LIMIT = 5",
            counter_demand="RETRY_LIMIT = 9",
        )
    with pytest.raises(ValidationError, match="two demands that agree"):
        MutuallyUnsatisfiable(
            subject="AC-5",
            amendment=AMEND,
            counter_subject="AC-6",
            path=CONTESTED,
            anchor=ANCHOR,
            subject_demand="RETRY_LIMIT = 5",
            counter_demand="RETRY_LIMIT = 5",
        )


def test_no_other_ground_may_name_a_counter_subject():
    for claim_type, evidence in (
        (UnsatisfiableAtBase, {"target_path": HOUSE}),
        (PremiseFalseAtBase, {"path": HOUSE, "premise": BASE_LINE}),
        (
            RequiresBreakingHouseRule,
            {"rule": RULE, "forbidden_construct": "add a fallback"},
        ),
    ):
        with pytest.raises(ValidationError, match="counter_subject"):
            claim_type(
                subject="AC-1",
                amendment=AMEND,
                counter_subject="AC-6",
                **evidence,
            )


async def test_the_reconciler_reads_the_resolved_base_and_not_the_head(
    repo, reconciler
):
    """Every amending case flips once the same claim is judged at the head."""
    amending = [claim for _, claim, expected in CASES if expected.ground is not None]
    at_head = [
        await reconciler.reconcile(
            claim=claim, issue_key=PARENT, cwd=str(repo), base_ref="HEAD"
        )
        for claim in amending
    ]
    assert len(amending) == len(AmendmentGround)
    assert [verdict.decision for verdict in at_head] == [
        AmendmentDecision.UPHELD
    ] * len(amending)


async def test_the_fixture_repository_actually_separates_its_two_commits(repo):
    reader = SubprocessGitSourceReader()
    base = await reader.resolve_commit(cwd=str(repo), ref=BASE)
    head = await reader.resolve_commit(cwd=str(repo), ref="HEAD")
    assert base != head
    assert await reader.find_source(cwd=str(repo), commit_sha=base, path=LATER) is None
    assert await reader.find_source(cwd=str(repo), commit_sha=head, path=LATER)
    rules = await reader.read_source(cwd=str(repo), commit_sha=base, path=RULES)
    assert RULE.encode() in rules.content
    assert LATER_RULE.encode() not in rules.content
    contested = await reader.read_source(cwd=str(repo), commit_sha=head, path=CONTESTED)
    assert ANCHOR.encode() not in contested.content


# --------------------------------------------------------------------------
# The default arm: what happens when the reconciler cannot substantiate the
# claim at all — a subject it does not hold, an address it cannot read, a
# ground the base does not bear out.
# --------------------------------------------------------------------------


def test_the_decision_domain_is_exactly_two_arms():
    """No third "unclear, proceed anyway" arm exists to fall into."""
    assert [decision.value for decision in AmendmentDecision] == ["upheld", "amended"]
    with pytest.raises(ValidationError):
        AmendmentVerdict.model_validate(
            {
                "subject": "AC-1",
                "decision": "unclear",
                "reason": "ground_not_reproduced",
            }
        )


def test_an_upheld_verdict_cannot_carry_a_ground_and_an_amendment_needs_one():
    with pytest.raises(ValidationError, match="refusal carries the reason"):
        AmendmentVerdict(
            subject="AC-1",
            decision=AmendmentDecision.UPHELD,
            ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
        )
    with pytest.raises(ValidationError, match="refusal carries the reason"):
        AmendmentVerdict(subject="AC-1", decision=AmendmentDecision.UPHELD)
    with pytest.raises(ValidationError, match="amendment carries the ground"):
        AmendmentVerdict(subject="AC-1", decision=AmendmentDecision.AMENDED)
    with pytest.raises(ValidationError, match="amendment carries the ground"):
        AmendmentVerdict(
            subject="AC-1",
            decision=AmendmentDecision.AMENDED,
            ground=AmendmentGround.UNSATISFIABLE_AT_BASE,
            reason=UpheldReason.GROUND_NOT_REPRODUCED,
        )


def test_a_claim_carries_no_reasoning_of_its_own():
    """The reconciler judges the repository, never the argument about it."""
    assert {
        claim_type.__name__: sorted(claim_type.model_fields)
        for claim_type in (
            UnsatisfiableAtBase,
            PremiseFalseAtBase,
            MutuallyUnsatisfiable,
            RequiresBreakingHouseRule,
        )
    } == {
        "UnsatisfiableAtBase": ["amendment", "ground", "subject", "target_path"],
        "PremiseFalseAtBase": ["amendment", "ground", "path", "premise", "subject"],
        "MutuallyUnsatisfiable": [
            "amendment",
            "anchor",
            "counter_demand",
            "counter_subject",
            "ground",
            "path",
            "subject",
            "subject_demand",
        ],
        "RequiresBreakingHouseRule": [
            "amendment",
            "forbidden_construct",
            "ground",
            "rule",
            "subject",
        ],
    }


@pytest.mark.parametrize("address", [DIRECTORY, SYMLINK])
async def test_an_address_the_base_cannot_read_is_upheld_and_never_an_error(
    repo, reconciler, address
):
    """The reader refuses these addresses; the reconciler still answers.

    Both are ordinary claimant mistakes — a directory and a symlink, each a
    real object at the base that is not one regular file. The reader raises
    on them, which is asserted here so the arm below is the reconciler's
    doing and not a lenient read.
    """
    with pytest.raises(GitSourceReadError):
        await SubprocessGitSourceReader().find_source(
            cwd=str(repo),
            commit_sha=await SubprocessGitSourceReader().resolve_commit(
                cwd=str(repo), ref=BASE
            ),
            path=address,
        )
    verdict = await reconciler.reconcile(
        claim=PremiseFalseAtBase(
            subject="AC-11", amendment=AMEND, path=address, premise=UNWRITTEN
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-11", UpheldReason.EVIDENCE_UNREADABLE)


async def test_an_unreadable_house_rules_document_is_upheld(repo, tracker):
    reconciler = AmendmentReconciler(
        source=SubprocessGitSourceReader(),
        tracker=tracker,
        house_rules_path=DIRECTORY,
    )
    verdict = await reconciler.reconcile(
        claim=RequiresBreakingHouseRule(
            subject="AC-9",
            amendment=AMEND,
            rule=RULE,
            forbidden_construct="add a fallback",
        ),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld("AC-9", UpheldReason.EVIDENCE_UNREADABLE)


@pytest.mark.parametrize(
    "address",
    ["docs/../src/house.py", "/etc/passwd", "src//house.py", "./src/house.py"],
)
def test_an_address_outside_the_repository_cannot_be_constructed(address):
    with pytest.raises(ValidationError, match="canonical relative path"):
        PremiseFalseAtBase(
            subject="AC-1", amendment=AMEND, path=address, premise=UNWRITTEN
        )


async def test_a_subject_outside_the_issues_criteria_is_refused_by_identity(
    repo, reconciler
):
    verdict = await reconciler.reconcile(
        claim=UnsatisfiableAtBase(subject=PARENT, amendment=AMEND, target_path=LATER),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    assert verdict == upheld(PARENT, UpheldReason.SUBJECT_NOT_A_CRITERION)


async def test_the_refusal_names_the_criterion_it_refused_and_no_other(
    repo, reconciler
):
    verdicts = [
        await reconciler.reconcile(
            claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
        )
        for _, claim, expected in CASES
        if expected.decision is AmendmentDecision.UPHELD
    ]
    assert [verdict.subject for verdict in verdicts] == [
        "AC-2",
        "AC-4",
        "AC-7",
        "AC-10",
    ]


async def test_a_refusal_leaves_every_criterion_body_byte_identical(
    repo, reconciler, tracker
):
    """And the same reconciler DOES rewrite one, so the assertion has teeth."""
    before = {
        criterion.issue_key: criterion.body
        for criterion in await tracker.read_criteria(issue_key=PARENT)
    }
    for _, claim, expected in CASES:
        if expected.decision is AmendmentDecision.UPHELD:
            await reconciler.reconcile(
                claim=claim, issue_key=PARENT, cwd=str(repo), base_ref=BASE
            )
    refused = {
        criterion.issue_key: criterion.body
        for criterion in await tracker.read_criteria(issue_key=PARENT)
    }
    assert refused == before
    assert tracker.issue_writes == []

    await reconciler.reconcile(
        claim=UnsatisfiableAtBase(subject="AC-1", amendment=AMEND, target_path=LATER),
        issue_key=PARENT,
        cwd=str(repo),
        base_ref=BASE,
    )
    after = {
        criterion.issue_key: criterion.body
        for criterion in await tracker.read_criteria(issue_key=PARENT)
    }
    assert after == {**before, "AC-1": AMEND}
    assert [key for key, _, _ in tracker.issue_writes] == ["AC-1"]


async def test_an_unresolvable_base_is_an_error_about_the_run_not_a_verdict(
    repo, reconciler
):
    """The base ref is the run's; only claimant addresses become refusals."""
    with pytest.raises(GitSourceReadError):
        await reconciler.reconcile(
            claim=UnsatisfiableAtBase(
                subject="AC-1", amendment=AMEND, target_path=LATER
            ),
            issue_key=PARENT,
            cwd=str(repo),
            base_ref="no-such-ref",
        )
