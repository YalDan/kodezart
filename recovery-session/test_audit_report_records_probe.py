"""Actual native publication must not summarize a record lost after verification."""
import pytest

from kodezart.domain.errors import AuditRunIncompleteError
from kodezart.types.domain.agent import WRITE_BACK_SCHEMA
from tests.integration.test_audit_runtime_native import native_audit, repository, server
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW


@pytest.mark.parametrize("mutation", ["deleted", "changed"])
async def test_native_summary_rereads_previously_verified_records(native_audit, mutation):
    audit, executor, board, _tracker, _git, _workspace, _repository = native_audit
    changed = False

    async def during(kwargs):
        nonlocal changed
        if kwargs["output_format"]["schema"] != WRITE_BACK_SCHEMA or changed:
            return
        records = [row for row in board.comments if row.body.startswith("[native-audit:")]
        if len(records) < 2:
            return
        first = records[0]
        if mutation == "deleted":
            board.comments.remove(first)
        else:
            first.body += "\nChanged after its completed verification."
        changed = True

    executor.during = during
    with pytest.raises(AuditRunIncompleteError):
        await audit.run(FIXTURE_NOW)
    assert changed
    assert audit.last_report.scopes[0].status == "incomplete"
    assert not [
        row for row in board.comments
        if row.issue_id == APPROVED_ISSUE and row.body.startswith("[native-audit:")
    ]
