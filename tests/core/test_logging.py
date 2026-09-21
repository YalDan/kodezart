"""The configured chain renders a traceback, not the object's repr.

Every soft-failure egress path logs with ``exc_info``; before the
formatter was added to the chain those tracebacks serialized as
``"<traceback object at 0x...>"``, which is a pointer into a process
that has since exited.
"""

from kodezart.core.logging import get_logger
from tests.core.test_logging_chain import configured_chain


def test_a_logged_exception_reaches_the_line_as_a_traceback() -> None:
    """Render native frames and restore the log sink before capture closes."""
    with configured_chain() as output:
        log = get_logger("tests.core.test_logging")
        try:
            msg = "boom"
            raise ValueError(msg)
        except ValueError:
            log.exception("stream_failed")
        out = output.getvalue()
    assert "stream_failed" in out
    assert "Traceback (most recent call last)" in out
    assert "ValueError: boom" in out
    assert "<traceback object at" not in out
