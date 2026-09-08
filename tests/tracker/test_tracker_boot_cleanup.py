"""The tracker boot owns its session until it returns it to the application."""

import pytest

from kodezart import main
from tests.tracker.test_tracker_boot_wiring import (
    _configure,
    _operation_toml,
)
from tests.tracker.test_tracker_boot_wiring import (
    server as server,
)
from tests.tracker.test_tracker_boot_wiring import (
    wired as wired,
)


class StartupError(Exception):
    pass


@pytest.mark.parametrize("failure", ["open", "reconciliation_log"])
async def test_native_tracker_boot_failure_closes_the_unreturned_transport(
    monkeypatch, tmp_path, wired, failure
):
    _configure(monkeypatch, tmp_path, _operation_toml())
    original_open = wired.open

    async def fail_open():
        await original_open()
        if failure == "open":
            raise StartupError(failure)

    class Log:
        async def ainfo(self, event, **kwargs):
            if (
                failure == "reconciliation_log"
                and event == "tracker_mappings_reconciled"
            ):
                raise StartupError(failure)

    monkeypatch.setattr(wired, "open", fail_open)
    monkeypatch.setattr(main, "get_logger", lambda _name: Log())
    app = main.create_app()
    with pytest.raises(StartupError, match=failure):
        async with app.router.lifespan_context(app):
            raise AssertionError("startup unexpectedly succeeded")
    assert wired.opens == wired.closes == 1


@pytest.mark.parametrize("startup_cancel", [False, True])
@pytest.mark.parametrize("cancel_close", [False, True])
@pytest.mark.parametrize("close_error", [False, True])
async def test_native_boot_settles_its_unreturned_caller_during_cancellation(
    monkeypatch, tmp_path, wired, startup_cancel, cancel_close, close_error
):
    import asyncio
    import traceback

    _configure(monkeypatch, tmp_path, _operation_toml())
    opened, closing, release_close = [asyncio.Event() for _ in range(3)]
    original_open, original_close = wired.open, wired.close
    cleanup_errors = []

    async def open_then_fail():
        await original_open()
        opened.set()
        if startup_cancel:
            await asyncio.Event().wait()
        raise StartupError("partial open")

    async def close_when_released():
        closing.set()
        await release_close.wait()
        await original_close()
        if close_error:
            raise StartupError("close failed")

    class Log:
        async def ainfo(self, event, **kwargs):
            pass

        async def aerror(self, event, **kwargs):
            cleanup_errors.append((event, kwargs))

    monkeypatch.setattr(wired, "open", open_then_fail)
    monkeypatch.setattr(wired, "close", close_when_released)
    monkeypatch.setattr(main, "get_logger", lambda _name: Log())
    app = main.create_app()

    async def run():
        async with app.router.lifespan_context(app):
            raise AssertionError("startup unexpectedly succeeded")

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(opened.wait(), 5)
        if startup_cancel:
            task.cancel()
        await asyncio.wait_for(closing.wait(), 5)
        if cancel_close:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert wired.closes == 0
        release_close.set()
        expected = (
            asyncio.CancelledError if startup_cancel or cancel_close else StartupError
        )
        with pytest.raises(expected) as caught:
            await asyncio.wait_for(task, 5)
        assert wired.opens == wired.closes == 1
        if close_error:
            assert len(cleanup_errors) == 1
            event, details = cleanup_errors[0]
            assert event == "tracker_boot_cleanup_failed"
            assert details["error"] == "close failed"
            assert details["error_kind"] == "StartupError"
            assert details["exc_info"] is not None
            if not startup_cancel and not cancel_close:
                rendered = "".join(traceback.format_exception(caught.value))
                assert "StartupError: partial open" in rendered
                assert "StartupError: close failed" in rendered
        else:
            assert not cleanup_errors
    finally:
        release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if wired.closes == 0:
            await original_close()
