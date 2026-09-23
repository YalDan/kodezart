"""One fold per signal, and what each fold's readings are scanned through."""

import pytest

from kodezart.domain import run_alarm_table
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_alarm_table import (
    ALARM_TABLE,
    AlarmTableError,
    alarm_raised,
    require_alarm_table,
)
from kodezart.domain.stream_signals import tally_regressed
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.run_alarm import AlarmBound, AlarmSignal, RunAlarm
from tests.domain import test_barren_tick as barren
from tests.domain import test_escalation_ageing as ageing
from tests.domain import test_lane_tally as lane_tally
from tests.domain import test_mandate_graph as mandate
from tests.domain import test_record_consistency as consistency
from tests.domain import test_record_superseded as superseded
from tests.domain import test_scope_tally as scope_tally
from tests.domain import test_stream_signals as streams
from tests.domain import test_surface_contention as contention
from tests.domain.test_run_alarm import DECLARED_SIGNALS
from tests.domain.test_stream_signals import HEAD, HOLDER, REGRESSED_PAIR


def test_every_member_of_the_vocabulary_has_a_fold():
    require_alarm_table()

    assert set(ALARM_TABLE) == set(AlarmSignal)


@pytest.mark.parametrize(
    "missing",
    [
        (AlarmSignal.TALLY_REGRESSED,),
        (AlarmSignal.TALLY_REGRESSED, AlarmSignal.COMPOSITION_SUBSTITUTED),
    ],
    ids=["one", "several"],
)
def test_a_member_with_no_fold_refuses_the_boot_naming_every_one(monkeypatch, missing):
    """Read at call time, so the refusal is of the table the process runs with."""
    monkeypatch.setattr(
        run_alarm_table,
        "ALARM_TABLE",
        {signal: row for signal, row in ALARM_TABLE.items() if signal not in missing},
    )

    with pytest.raises(AlarmTableError) as caught:
        require_alarm_table()

    assert caught.value.missing == missing
    for signal in missing:
        assert signal.value in str(caught.value)


#: The whole scans column, written out member by member rather than read off
#: the table: the issue listing for every signal whose readings come through
#: rosters, criterion states or stage markers, and no boot probe for the
#: signals read off comments and records alone.
DECLARED_SCANS = {
    AlarmSignal.TALLY_UNMOVED: {PassSignal.issues_changed},
    AlarmSignal.TALLY_REGRESSED: {PassSignal.issues_changed},
    AlarmSignal.LAPSE_UNDISCHARGED: {PassSignal.issues_changed},
    AlarmSignal.ESCALATION_AGEING: set(),
    AlarmSignal.WRITE_BACK_MISSING: set(),
    AlarmSignal.SURFACE_CONTENDED: set(),
    AlarmSignal.RECORD_SUPERSEDED: set(),
    AlarmSignal.COMPOSITION_SUBSTITUTED: set(),
    AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH: set(),
    AlarmSignal.COMMITS_AHEAD_OF_RECORD: set(),
    AlarmSignal.RULINGS_OUTPACE_CLOSURES: {PassSignal.issues_changed},
    AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE: {PassSignal.issues_changed},
}


def test_every_alarm_declares_exactly_the_scans_its_readings_come_through():
    """The column is pinned whole, so no member's scans can drift unseen."""
    assert {
        signal: set(row.scans) for signal, row in ALARM_TABLE.items()
    } == DECLARED_SCANS


def test_a_stored_record_is_raised_exactly_when_its_readings_replay_to_one():
    on, firing, clean = REGRESSED_PAIR
    raised = tally_regressed(
        subject=on, readings=firing, raised_at_sha=HEAD, raised_by=HOLDER
    )
    assert raised is not None
    quiet = RunAlarm(
        subject=on,
        signal=AlarmSignal.TALLY_REGRESSED,
        readings=clean,
        bound=None,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )

    assert alarm_raised(raised)
    assert not alarm_raised(quiet)
    assert not alarm_raised(None)


def test_a_record_carrying_a_bound_its_readings_never_crossed_refuses():
    on, firing, _ = REGRESSED_PAIR
    raised = tally_regressed(
        subject=on, readings=firing, raised_at_sha=HEAD, raised_by=HOLDER
    )
    assert raised is not None
    forged = raised.model_copy(
        update={
            "bound": AlarmBound(
                config_field="run_alarm_max_commits_without_closure",
                configured_value=1,
                observed_value=2,
            )
        }
    )

    with pytest.raises(RunShapeReadError, match="replays to a bound"):
        alarm_raised(forged)


# ---------------------------------------------------------------------------
# Every folded signal is a member; every widening an arm of its member.
# ---------------------------------------------------------------------------

_SURFACE = contention.address()

#: Per member: its subject, a reading that makes its fold return an alarm, and
#: a near-identical reading — one fact apart — that must return none. Built
#: from each signal's own test module, so the pair is the one that signal's
#: tests already describe.
FOLD_PAIRS = {
    AlarmSignal.TALLY_UNMOVED: (
        lane_tally.LANE,
        lane_tally.firing(),
        lane_tally.firing(bound=2),
    ),
    AlarmSignal.TALLY_REGRESSED: streams.REGRESSED_PAIR,
    AlarmSignal.LAPSE_UNDISCHARGED: streams.LAPSE_PAIR,
    AlarmSignal.ESCALATION_AGEING: (
        ageing.SUBJECT,
        ageing.readings(),
        ageing.readings(max_commits=2),
    ),
    AlarmSignal.WRITE_BACK_MISSING: (
        consistency.surface_subject(consistency.surface()),
        consistency.write_readings(consistency.surface()),
        consistency.write_readings(consistency.surface(), present=True),
    ),
    AlarmSignal.SURFACE_CONTENDED: (
        contention.subject(_SURFACE),
        contention.readings(_SURFACE),
        contention.readings(_SURFACE, holders=("job/run-a",)),
    ),
    AlarmSignal.RECORD_SUPERSEDED: (
        superseded.subject(),
        superseded.readings(),
        superseded.readings(event_value="red"),
    ),
    AlarmSignal.COMPOSITION_SUBSTITUTED: streams.SUBSTITUTED_PAIR,
    AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH: (
        barren.SUBJECT,
        barren.readings(),
        barren.readings(closed=("EXT/43",)),
    ),
    AlarmSignal.COMMITS_AHEAD_OF_RECORD: (
        consistency.lane_subject(),
        consistency.commit_readings(count=3),
        consistency.commit_readings(count=2),
    ),
    AlarmSignal.RULINGS_OUTPACE_CLOSURES: (
        mandate.SUBJECT,
        mandate.ruling_inputs(),
        mandate.ruling_inputs(closed=("criterion/open",)),
    ),
    AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE: mandate.UNCROSSED_PAIR,
}

#: The two widenings, each an extra pair on the member it widens, imported
#: from the module that owns the arm.
WIDENINGS = {
    "scope arm": (AlarmSignal.TALLY_UNMOVED, *scope_tally.SCOPE_ARM_PAIR),
    "cross-run": (AlarmSignal.SURFACE_CONTENDED, *contention.CROSS_RUN_PAIR),
}


def _fold(member, subject, readings):
    return ALARM_TABLE[member].fold(
        subject=subject, readings=readings, raised_at_sha=HEAD, raised_by=HOLDER
    )


def test_every_member_carries_its_exact_string_value():
    """The member set is pinned once, where the vocabulary's own tests pin it."""
    assert {member.name: member.value for member in AlarmSignal} == dict(
        DECLARED_SIGNALS
    )


def test_every_member_brings_a_fold_pair():
    """Whatever spelling the parametrization below has, a member with no pair."""
    assert set(FOLD_PAIRS) == set(AlarmSignal)


@pytest.mark.parametrize("member", list(AlarmSignal), ids=lambda member: member.value)
def test_every_member_fires_on_one_fixture_and_is_quiet_on_its_twin(member):
    """Derived from the vocabulary: a member with no pair fails here."""
    subject, firing, clean = FOLD_PAIRS[member]

    alarm = _fold(member, subject, firing)

    assert alarm is not None
    assert alarm.signal is member
    assert alarm.readings == firing
    assert _fold(member, subject, clean) is None


@pytest.mark.parametrize("widening", sorted(WIDENINGS))
def test_each_widening_is_a_fixture_pair_on_its_members_own_fold(widening):
    member, subject, firing, clean = WIDENINGS[widening]

    alarm = _fold(member, subject, firing)

    assert alarm is not None
    assert alarm.signal is member
    assert _fold(member, subject, clean) is None


def test_each_member_is_answered_by_the_function_named_for_it():
    """A widening promoted to a member would need a function of its own."""
    folds = [ALARM_TABLE[member].fold for member in AlarmSignal]

    assert [fold.__name__ for fold in folds] == [member.value for member in AlarmSignal]
    assert len(set(folds)) == len(folds)
