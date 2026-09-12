"""The actual audit consumers share native key refusal before downstream work."""

from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import (
    AuditClaimReadError,
    AuditEvidenceReadError,
    CriterionResolutionError,
)
from tests.fakes import FakeCIMonitor
from tests.services.test_audit_sources import reader
from tests.tracker import test_audit_evidence as evidence
from tests.tracker import test_audit_forge as forge

server = evidence.server
claim_setup = evidence.claim_setup
setup = evidence.setup
forge_setup = forge.setup


@pytest.mark.parametrize("consumer", ["claim", "evidence", "source", "forge"])
@pytest.mark.parametrize("damage", ["missing", "multiple", "noncriterion"])
async def test_actual_audit_entry_preserves_typed_key_failure_without_side_effects(
    setup, forge_setup, tracker, tracker_writes, monkeypatch, consumer, damage
):
    build, runner, git, source, cache, workspace, _, claims = setup
    ci = FakeCIMonitor()
    requested = "absent/二" if damage == "missing" else evidence.CHILD
    if damage != "missing":
        rows = list(await tracker.read_criteria(issue_key=evidence.ROOT))
        assert len(rows) == 1
        rows = (
            rows + rows
            if damage == "multiple"
            else [rows[0].model_copy(update={"issue_labels": frozenset()})]
        )
        monkeypatch.setattr(tracker, "read_criteria", AsyncMock(return_value=rows))
    request = evidence.REQUEST.model_copy(update={"criterion_key": requested})
    before = tracker_writes()
    with pytest.raises(
        AuditClaimReadError if consumer == "claim" else AuditEvidenceReadError
    ) as raised:
        if consumer == "claim":
            await claims.verify(request)
        elif consumer == "evidence":
            await build().observe(request)
        elif consumer == "source":
            await reader(setup, tracker).read(request)
        else:
            await forge_setup(ci).observe(
                forge.REQUEST.model_copy(update={"criterion_key": requested})
            )
    cause = raised.value.__cause__
    assert isinstance(cause, CriterionResolutionError)
    assert cause.issue_key == evidence.ROOT and cause.criterion_key == requested
    assert evidence.ROOT in str(raised.value) and requested in str(raised.value)
    assert not runner.calls and not git.calls and not source.calls
    assert not cache.calls and not workspace.calls and not ci.calls
    assert not ci.rerun_calls and not ci.declaration_calls
    assert tracker_writes() == before
