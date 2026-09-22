"""One fold per signal, and what each fold's readings are scanned through."""

import pytest

from kodezart.domain import run_alarm_table
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.lane_alarms import OBSERVED_ALARMS
from kodezart.domain.run_alarm_table import (
    ALARM_TABLE,
    AlarmTableError,
    alarm_raised,
    alarm_scans,
    require_alarm_table,
)
from kodezart.domain.stream_signals import tally_regressed
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.run_alarm import AlarmBound, AlarmSignal, RunAlarm
from tests.domain.test_stream_signals import HEAD, HOLDER, REGRESSED_PAIR


def test_every_member_of_the_vocabulary_has_a_fold():
    require_alarm_table()

    assert set(ALARM_TABLE) == set(AlarmSignal)


def test_a_member_with_no_fold_refuses_the_boot_naming_every_one(monkeypatch):
    """Read at call time, so the refusal is of the table the process runs with."""
    missing = (AlarmSignal.TALLY_REGRESSED, AlarmSignal.COMPOSITION_SUBSTITUTED)
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


def test_the_supervisors_alarms_declare_the_issue_listing_and_nothing_else():
    """Named per alarm, so a refusal says which alarms need the scan."""
    assert alarm_scans(OBSERVED_ALARMS) == {
        PassSignal.issues_changed: (
            AlarmSignal.LAPSE_UNDISCHARGED,
            AlarmSignal.TALLY_REGRESSED,
            AlarmSignal.TALLY_UNMOVED,
        )
    }


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
