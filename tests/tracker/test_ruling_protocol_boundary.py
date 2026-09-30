"""Published reader ports retain their actual runtime structural checks."""

from kodezart.core.protocols import TrackerCommentReader
from tests.fakes import FakeTrackerPort


def test_comment_reader_runtime_contract_accepts_actual_adapter():
    assert isinstance(FakeTrackerPort(), TrackerCommentReader)
    assert not isinstance(object(), TrackerCommentReader)
