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
