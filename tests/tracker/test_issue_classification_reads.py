"""Classification capability is distinct from readiness and phase policy."""

import pytest

from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import FakeTrackerPort
from tests.tracker.test_scope_planning import LABELS, native_tracker
from tests.tracker.test_scope_reads import ScopeMcpServer


@pytest.mark.parametrize("missing", ["criterion", "decision", "tracker"])
def test_native_requires_every_declared_classification_without_io(missing):
    server = ScopeMcpServer()
    labels = {key: value for key, value in LABELS.items() if key != missing}
    tracker = native_tracker(server, labels)
    with pytest.raises(OperationMemberAbsentError, match=missing):
        tracker.require_issue_classification_reads()
    assert server.calls == []


def test_complete_native_and_fake_classifications_need_no_read_or_write():
    server = ScopeMcpServer()
    native_tracker(server, LABELS).require_issue_classification_reads()
    fake = FakeTrackerPort()
    fake.require_issue_classification_reads()
    assert server.calls == []
    assert fake.issue_reads == []
    assert fake.issue_writes == []


@pytest.mark.parametrize("blank", ["", "  "])
@pytest.mark.parametrize("key", ["criterion", "tracker", "decision", "phase-complete"])
def test_blank_required_mapping_is_not_readable(key, blank):
    server = ScopeMcpServer()
    tracker = native_tracker(server, {**LABELS, key: blank})
    with pytest.raises(OperationMemberAbsentError, match=key):
        tracker.require_issue_classification_reads(additional_keys=frozenset({key}))
    assert server.calls == []


def test_additional_phase_mapping_must_exist_before_any_read():
    server = ScopeMcpServer()
    tracker = native_tracker(server, LABELS)
    with pytest.raises(OperationMemberAbsentError, match="phase-complete"):
        tracker.require_issue_classification_reads(
            additional_keys=frozenset({"phase-complete"})
        )
    assert server.calls == []
