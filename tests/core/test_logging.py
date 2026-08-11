"""The configured chain renders a traceback, not the object's repr.

Every soft-failure egress path logs with ``exc_info``; before the
formatter was added to the chain those tracebacks serialized as
``"<traceback object at 0x...>"``, which is a pointer into a process
that has since exited.
"""

import pytest

from kodezart.core.logging import configure_logging, get_logger


def test_a_logged_exception_reaches_the_line_as_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rendered line carries the frames, and no object repr survives."""
    configure_logging(log_level="INFO", pretty=False)
    log = get_logger("tests.core.test_logging")

    try:
        msg = "boom"
        raise ValueError(msg)
    except ValueError:
        log.exception("stream_failed")

    out = capsys.readouterr().out
    assert "stream_failed" in out
    assert "Traceback (most recent call last)" in out
    assert "ValueError: boom" in out
    assert "<traceback object at" not in out
