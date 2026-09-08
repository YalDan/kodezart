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
