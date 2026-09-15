"""Published reader ports retain their actual runtime structural checks."""

from kodezart.core.protocols import TrackerCommentReader, TrackerPort


def test_comment_reader_runtime_contract_accepts_actual_adapter(
    tracker: TrackerPort,
) -> None:
    """Every registered implementation, adapter and double alike, satisfies it.

    Taken over the ``tracker`` fixture rather than over a hand-built double:
    a structural contract the shipped adapter fails is exactly the defect
    this check exists for, and asserting it against the fake alone answers
    a question nobody asked.
    """
    assert isinstance(tracker, TrackerCommentReader)


def test_comment_reader_runtime_contract_refuses_a_foreign_object() -> None:
    """The check discriminates: an object without the read is not a reader."""
    assert not isinstance(object(), TrackerCommentReader)
