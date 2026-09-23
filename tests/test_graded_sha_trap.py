"""The two revisions trapped at run time (KOD-413, KOD-696, KOD-596).

The static guard beside this module, ``test_graded_sha_comparison_sites``,
reads spellings.  This module reads nothing: it hands the composed readers
a graded sha and a head sha that record every comparison made of them, and
asks where each comparison happened.  A comparison the rule does not make
is red whatever it is spelled as.

**The trapped value.**  ``TrappedSha`` is a ``str`` whose ``__eq__`` and
``__ne__`` note the scope and the statement of the frame that compared it,
and whose hash is ``str``'s, so it keys a dict and sits in a set exactly as
the plain value does.  Python tries the right operand's reflected method
first when its type is a subclass of the left's that overrides the method,
so a plain string compared with a trapped one is caught too: trapping both
the graded sha and the head catches a comparison where either side is
trapped, and a value formatted or copied to a plain ``str`` on one side is
still caught by the other.

**The drive.**  Each composed reader that consults the rule -- the lane
reading, the audit observation and the audit evidence reader, the guard's
own ``CALLERS`` -- and the two registered readers that carry the value
past a decision, the lane state writer's cross-off and the restamp
observation, is run on its own fixtures with both values trapped.  A
record validated from a trapped value holds a plain copy, so each record
is copied with the trap in place of the value instead.

**The assertion.**  Every comparison of the graded sha made inside the
package happens in the rule, or in a registered scope at a statement its
row pins.  A comparison at any other statement is a reading of the pair
nobody licensed, and the rule is the one scope that weighs the pair.

**The stated limit.**  A value copied into a plain ``str`` on both sides
before the comparison -- sliced, passed through ``str()``, lowered,
formatted -- is no longer trapped, and ``test_a_value_copied_to_a_plain_str_
on_both_sides_is_past_the_trap`` holds that as a fact.
"""

import ast
import sys
from functools import cache
from pathlib import Path
from types import FrameType
from typing import NamedTuple

from kodezart.chains import audit_evidence as audit_chain
from kodezart.chains.audit_evidence import AuditRestampVerifier
from kodezart.domain.criterion_cross_off import cross_offs_for, evaluation_observation
from kodezart.domain.fire_spec import criterion_ref
from kodezart.domain.lapse import GradedState, graded_state, held_standing
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.criteria import CriterionId
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    RederivationClass,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.domain.test_lapse import EXERCISED, GRADED, HEAD, digest, standing
from tests.fakes import make_tracker_issue
from tests.services.test_lane_state_writer import (
    CRITERIA,
    binding,
    criteria_board,
    dispatched,
    graded,
    lane_repo,
    tick,
    writer,
)
from tests.test_graded_sha_comparison_sites import (
    ANONYMOUS,
    CALLERS,
    CLAUSES,
    REGISTERED,
    RULE_SITE,
    SHIPPED,
    SOURCE,
    _render,
)
from tests.tracker import test_audit_evidence as audit_fixtures
from tests.tracker.conftest import clock as clock
from tests.tracker.conftest import tracker as tracker
from tests.tracker.test_audit_claim import CHILD, REQUEST, ROOT
from tests.tracker.test_audit_claim import HEAD as AUDIT_HEAD
from tests.tracker.test_audit_evidence import PRIOR, TEST

#: The audit evidence reader's own fixtures, under the names its own tests
#: request them by, so the reader is built here exactly as it is there.
claim_setup = audit_fixtures.claim_setup
server = audit_fixtures.server
audit_setup = audit_fixtures.setup

GRADED_SIDE = "graded"
HEAD_SIDE = "head"
OTHER_SIDE = "other"


class Comparison(NamedTuple):
    """One comparison of a trapped value: where it happened, and of what."""

    #: ``module::qualname`` inside the package, as the register names a
    #: scope, or None for a frame outside it (a fixture, a library).
    site: str | None
    #: The statement the comparison is written in, rendered as the register
    #: pins a use, or None outside the package.
    clause: str | None
    #: Which of the two revisions the operands were: the trapped side, and
    #: the other operand's side read off its type or, for a plain string,
    #: its value.
    sides: frozenset[str]


class ShaTrap:
    """The two revisions of one reading, and every comparison made of either."""

    def __init__(self, *, graded: str, head: str) -> None:
        assert graded != head, "a trap needs two revisions it can tell apart"
        self.values = {GRADED_SIDE: graded, HEAD_SIDE: head}
        self.comparisons: list[Comparison] = []

    def graded(self) -> "TrappedSha":
        return TrappedSha(self.values[GRADED_SIDE], GRADED_SIDE, self)

    def head(self) -> "TrappedSha":
        return TrappedSha(self.values[HEAD_SIDE], HEAD_SIDE, self)

    def side_of(self, value: object) -> str:
        if isinstance(value, TrappedSha):
            return value.side
        if isinstance(value, str):
            for side, held in self.values.items():
                if str.__eq__(held, value):
                    return side
        return OTHER_SIDE

    def record(self, one: "TrappedSha", other: object, frame: FrameType) -> None:
        site, clause = _written_at(frame)
        sides = frozenset({one.side, self.side_of(other)})
        self.comparisons.append(Comparison(site, clause, sides))

    def weighed(self) -> list[Comparison]:
        """Every comparison of the graded sha made inside the package."""
        return [
            comparison
            for comparison in self.comparisons
            if comparison.site is not None and GRADED_SIDE in comparison.sides
        ]

    def weighed_in_the_rule(self) -> list[Comparison]:
        """The comparisons of the graded sha with the head the rule made."""
        return [
            comparison
            for comparison in self.weighed()
            if comparison.site == RULE_SITE
            and comparison.sides == {GRADED_SIDE, HEAD_SIDE}
        ]

    def unpinned(self) -> list[str]:
        """Each comparison of the graded sha outside the rule and off every row."""
        return [
            f"{comparison.site}: {comparison.clause}"
            for comparison in self.weighed()
            if not _permitted(comparison)
        ]


class TrappedSha(str):
    """A sha that records every comparison made of it, and where.

    Equality and inequality are ``str``'s own, noted; the hash is ``str``'s
    unchanged, so the value keys and hashes as the plain one does.
    """

    side: str
    trap: ShaTrap

    def __new__(cls, value: str, side: str, trap: ShaTrap) -> "TrappedSha":
        made = super().__new__(cls, value)
        made.side = side
        made.trap = trap
        return made

    def __eq__(self, other: object) -> bool:
        self.trap.record(self, other, sys._getframe(1))
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        self.trap.record(self, other, sys._getframe(1))
        return str.__ne__(self, other)

    __hash__ = str.__hash__


@cache
def _tree(module: str) -> ast.Module | None:
    text = SHIPPED.get(module)
    return None if text is None else ast.parse(text)


def _position(frame: FrameType) -> tuple[int, int]:
    """The line and column of the instruction *frame* is executing."""
    positions = list(frame.f_code.co_positions())
    index = frame.f_lasti // 2
    lineno, _, column, _ = positions[index] if index < len(positions) else (None,) * 4
    if lineno is None:
        return frame.f_lineno or 0, 0
    return lineno, column or 0


def _contains(node: ast.AST, line: int, column: int) -> bool:
    start = getattr(node, "lineno", None)
    if start is None:
        return False
    end = (node.end_lineno or start, node.end_col_offset or 0)
    return (start, node.col_offset) <= (line, column) <= end


def _clause_at(module: str, line: int, column: int) -> str | None:
    """The innermost clause of the shipped module written at a position."""
    tree = _tree(module)
    if tree is None:
        return None
    within = [
        node
        for node in ast.walk(tree)
        if isinstance(node, CLAUSES) and _contains(node, line, column)
    ]
    if not within:
        return None
    return _render(max(within, key=lambda node: (node.lineno, node.col_offset)))


def _written_at(frame: FrameType) -> tuple[str | None, str | None]:
    """The scope and the clause *frame* is executing, inside the package."""
    try:
        module = Path(frame.f_code.co_filename).resolve().relative_to(SOURCE)
    except ValueError:
        return None, None
    qualname = frame.f_code.co_qualname.replace(".<locals>", "")
    site = f"{module.as_posix()}::{qualname}"
    return site, _clause_at(module.as_posix(), *_position(frame))


def _permitted(comparison: Comparison) -> bool:
    """The rule's own comparison, or one made at a statement a row pins.

    A comprehension inlined into its function runs in the function's frame,
    so the rows of the anonymous scopes nested in the site are read too.
    """
    if comparison.site == RULE_SITE:
        return True
    sites = [comparison.site, *(f"{comparison.site}.{a}" for a in ANONYMOUS.values())]
    return any(
        site in REGISTERED and comparison.clause in REGISTERED[site].uses
        for site in sites
    )


def trapped(cross_off: CriterionCrossOff, trap: ShaTrap) -> CriterionCrossOff:
    """The cross-off with its graded sha trapped.

    Validation copies a value into a plain ``str``, so the record is copied
    with the trap in place of the value instead of being built from it.
    """
    evidence = cross_off.evidence.model_copy(update={"graded_sha": trap.graded()})
    return cross_off.model_copy(update={"evidence": evidence})


def test_the_trap_records_the_scope_and_the_statement_of_each_comparison():
    """The rule's one comparison of the pair is noted where it is written.

    Read from the rule itself: the site is the rule's, the statement is
    the rule's expression, and the sides are the two revisions.  A plain
    string compared with a trapped one is caught through the reflected
    operand, and a plain string equal to one revision is read as that side.
    """
    trap = ShaTrap(graded=GRADED, head=HEAD)
    assert graded_state(graded_sha=trap.graded(), head_sha=trap.head()) is (
        GradedState.lapsed
    )
    [comparison] = trap.comparisons
    assert comparison.site == RULE_SITE
    assert comparison.clause is not None
    assert comparison.clause.startswith("return ")
    assert "graded_sha == head_sha" in comparison.clause
    assert comparison.sides == {GRADED_SIDE, HEAD_SIDE}
    assert trap.weighed_in_the_rule() == [comparison]
    assert trap.unpinned() == []
    assert hash(trap.graded()) == hash(GRADED)
    assert {trap.graded(): 1}[GRADED] == 1
    assert GRADED != trap.head()
    assert trap.comparisons[-1].sides == {GRADED_SIDE, HEAD_SIDE}
    assert trap.comparisons[-1].site is None
    assert "c" * 40 != trap.graded()
    assert trap.comparisons[-1].sides == {GRADED_SIDE, OTHER_SIDE}


def test_a_value_copied_to_a_plain_str_on_both_sides_is_past_the_trap():
    """The stated limit, held as a fact.

    A copy of the value is a plain string: sliced, passed through ``str``,
    lowered, formatted or joined, it compares without a trace.  One trapped
    side is enough, so a copy on one side alone is still caught.
    """
    trap = ShaTrap(graded=GRADED, head=HEAD)
    graded_sha, head = trap.graded(), trap.head()
    copies = (
        str(graded_sha) != str(head),
        graded_sha[:] != head[:],
        graded_sha.lower() != head.lower(),
        f"{graded_sha}" != f"{head}",
        format(graded_sha) != format(head),
        "".join(graded_sha) != "".join(head),
    )
    assert all(copies)
    assert trap.comparisons == []
    assert str(graded_sha) != head
    assert format(head) != graded_sha
    assert [comparison.sides for comparison in trap.comparisons] == [
        {GRADED_SIDE, HEAD_SIDE},
        {GRADED_SIDE, HEAD_SIDE},
    ]


def test_the_lane_reading_weighs_the_pair_in_the_rule_alone():
    """The partition hands both revisions to the rule and weighs nothing itself.

    A standing path-bound grading whose paths moved, and a cheap one: the
    digest map is keyed by the graded sha, which the trap reads as a
    comparison at the statement the row pins, and the pair meets in the
    rule.
    """
    trap = ShaTrap(graded=GRADED, head=HEAD)
    prior = [
        trapped(
            standing(
                "alpha",
                rederivation_class=RederivationClass.observed,
                exercised_paths=(EXERCISED,),
            ),
            trap,
        ),
        trapped(standing("beta"), trap),
    ]
    partition = held_standing(
        prior=prior,
        head_sha=trap.head(),
        changesets={GRADED: digest(f"{EXERCISED}lapse.py")},
        base_stale=False,
    )
    assert [str(cross_off.criterion) for cross_off in partition.lapsed] == ["alpha"]
    assert trap.weighed_in_the_rule()
    assert trap.unpinned() == []
    site = "domain/lapse.py::held_standing"
    keyed = [c for c in trap.weighed() if c.site == site]
    assert keyed
    assert all(c.sides == {GRADED_SIDE} for c in keyed)
    assert all(c.clause in REGISTERED[site].uses for c in keyed)


def observation(trap: ShaTrap | None = None) -> AuditEvidenceObservation:
    """A finished claim's observation at a head its grading has lapsed at."""
    made = AuditEvidenceObservation(
        criterion=make_tracker_issue(
            CHILD, state_name="Done", state_kind=WorkflowStateKind.COMPLETED
        ),
        recorded_evidence=CriterionEvidence(graded_sha=PRIOR, test=TEST),
        head_sha=AUDIT_HEAD,
        record_ref="record",
        verdict=AuditVerdict.UNVERIFIABLE,
        current_claim=None,
    )
    if trap is None:
        return made
    evidence = made.recorded_evidence.model_copy(update={"graded_sha": trap.graded()})
    return made.model_copy(
        update={"recorded_evidence": evidence, "head_sha": trap.head()}
    )


def test_the_audit_observation_weighs_the_pair_in_the_rule_alone():
    """The observation's lapse reading is the rule's answer, weighed nowhere else."""
    trap = ShaTrap(graded=PRIOR, head=AUDIT_HEAD)
    assert observation().is_lapse
    assert observation(trap).is_lapse
    assert trap.weighed_in_the_rule()
    assert trap.unpinned() == []


async def test_the_audit_evidence_reader_weighs_the_pair_in_the_rule_alone(
    audit_setup, monkeypatch
):
    """The reader resolves the graded sha against itself and hands the pair to the rule.

    The head comes from the remote through the git port, so the port's
    answer is trapped; the graded sha is parsed out of the criterion's body
    inside the reader, so the parser it imports hands back the same record
    with its sha trapped.
    """
    build, _, git, *_ = audit_setup
    trap = ShaTrap(graded=PRIOR, head=AUDIT_HEAD)
    assert git._remote_branch_shas
    git._remote_branch_shas = {
        branch: trap.head() for branch in git._remote_branch_shas
    }
    parse = audit_chain.parse_audit_evidence
    monkeypatch.setattr(
        audit_chain,
        "parse_audit_evidence",
        lambda body: parse(body).model_copy(update={"graded_sha": trap.graded()}),
    )
    lapsed = await build().observe(REQUEST)
    assert lapsed.is_lapse
    assert lapsed.verdict is AuditVerdict.UNVERIFIABLE
    assert trap.weighed_in_the_rule()
    assert trap.unpinned() == []


async def test_the_restamp_reading_compares_the_graded_sha_only_as_its_rows_say(
    tracker,
):
    """Recorded against recorded: the trace compares the graded sha, never a head."""
    trap = ShaTrap(graded=PRIOR, head=AUDIT_HEAD)
    await tracker.post_run_event(
        issue_key=ROOT,
        event=LaneRunEvent(
            kind=RunEventKind.CRITERION_REFUTED,
            lane_key=REQUEST.lane_key,
            subject_key=CHILD,
            graded_sha=PRIOR,
        ),
    )
    evidence = CriterionEvidence(graded_sha=PRIOR, test=TEST).model_copy(
        update={"graded_sha": trap.graded()}
    )
    trace = await AuditRestampVerifier(events=tracker).observe(
        request=REQUEST, evidence=evidence
    )
    assert trace is not None
    assert trace.verdict is AuditVerdict.HOLDS
    assert trap.weighed()
    assert {comparison.sides for comparison in trap.weighed()} == {
        frozenset({GRADED_SIDE})
    }
    assert trap.unpinned() == []


async def test_the_lane_state_writer_takes_a_lapsed_grading_back_without_weighing_it():
    """The writer records the graded sha and compares it with no head."""
    port = criteria_board()
    lane_state = writer(port, lane_repo())
    key = CRITERIA[0]
    await tick(lane_state, sha="1" * 40)
    trap = ShaTrap(graded="1" * 40, head="2" * 40)
    standing_gradings = cross_offs_for(
        results=graded([key]),
        graded_sha="1" * 40,
        observation=evaluation_observation(session_id="eval-session", iteration=1),
        reasons={},
    )
    lapsed = cross_offs_for(
        results=graded([key]),
        graded_sha=trap.head(),
        observation=evaluation_observation(session_id="eval-session", iteration=2),
        reasons={},
        standing=tuple(trapped(cross_off, trap) for cross_off in standing_gradings),
        reading={criterion_ref(CriterionId(key)): GradedState.lapsed},
    )
    await lane_state.write_cross_offs(
        lane=binding(),
        dispatched=dispatched([key]),
        cross_offs=tuple(trapped(cross_off, trap) for cross_off in lapsed),
    )
    assert port.issues[key].state_kind is WorkflowStateKind.UNSTARTED
    assert [c for c in trap.weighed() if HEAD_SIDE in c.sides] == []
    assert trap.unpinned() == []


#: The drive each consultation of the rule, and each registered reader that
#: carries the value past a decision, runs through with both values trapped.
DRIVEN = {
    "domain/lapse.py::held_standing": (
        test_the_lane_reading_weighs_the_pair_in_the_rule_alone
    ),
    "types/domain/audit_evidence.py::AuditEvidenceObservation.is_lapse": (
        test_the_audit_observation_weighs_the_pair_in_the_rule_alone
    ),
    "chains/audit_evidence.py::AuditEvidenceVerifier._observe": (
        test_the_audit_evidence_reader_weighs_the_pair_in_the_rule_alone
    ),
    "chains/audit_evidence.py::AuditRestampVerifier.observe": (
        test_the_restamp_reading_compares_the_graded_sha_only_as_its_rows_say
    ),
    "services/lane_state_writer.py::TrackerLaneStateWriter.write_cross_offs": (
        test_the_lane_state_writer_takes_a_lapsed_grading_back_without_weighing_it
    ),
}


def test_every_consultation_of_the_rule_is_driven_with_both_values_trapped():
    """The drive list is derived from the guard's own readers of the rule."""
    assert CALLERS
    assert frozenset(CALLERS) <= frozenset(DRIVEN), sorted(
        frozenset(CALLERS) - frozenset(DRIVEN)
    )
    assert frozenset(DRIVEN) <= frozenset(REGISTERED)
    assert all(
        drive.__module__ == __name__ and drive.__name__.startswith("test_")
        for drive in DRIVEN.values()
    )
