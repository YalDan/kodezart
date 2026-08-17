"""Session-scoped emission of the probe ledger.

Module scope would print only what the module that owns the fixture
recorded, which loses the rows of every other probe module selected in the
same run -- and a measurement that is not reported is not evidence.
"""

from collections.abc import Iterator

import pytest

from tests.probes.recording import RECORDS, render_table


@pytest.fixture(scope="session", autouse=True)
def emit_results_table(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    if not RECORDS:
        return
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    reporter.write_line(render_table(RECORDS))
