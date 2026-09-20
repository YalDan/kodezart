"""Every audit write reaches the sanitization gate before it reaches the backend.

Asserted at the two logs — the gate's own record and the workspace's tool
call log — and never on the text of a report. The marker prefixes the check
scans for are read off the configured operation, so an audit that wrote under
a third marker purpose would be scanned too rather than slipping past a list
written out here.
"""

import pytest

from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.gating import GateDecision, GateVerdict
from tests.fakes import PassThroughGate
from tests.integration.test_audit_runtime_native import (
    build_native_audit,
    landed,
    native_operation,
    state_writes,
    unstarted_state,
)
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.test_audit_evidence_git import repository as repository
from tests.tracker.test_audit_requests import CHILD, ROOT
from tests.tracker.test_state_history import server as server

__all__ = ["repository", "server"]

#: The purposes the audit lane writes comments under. Their spellings come
#: from the configuration, not from this module.
AUDIT_PURPOSES = ("audit", "escalation")


def audit_prefixes(repo_url):
    prefixes = native_operation(repo_url).marker_prefixes
    return tuple(
        f"[{configured_marker_prefix(prefixes, purpose=purpose)}:"
        for purpose in AUDIT_PURPOSES
    )


class OrderedGate(PassThroughGate):
    """Passes everything, and records where in the tool log each call stood."""

    def __init__(self, server) -> None:
        super().__init__()
        self._server = server
        #: (destination, content, number of tool calls made so far).
        self.marks: list[tuple[str, str, int]] = []

    async def gate(self, **kwargs) -> GateDecision:
        decision = await super().gate(**kwargs)
        self.marks.append(
            (kwargs["destination"].value, kwargs["content"], len(self._server.calls))
        )
        return decision


class BlockingGate(PassThroughGate):
    """Blocks any payload carrying *substring*, and passes everything else."""

    def __init__(self, substring: str) -> None:
        super().__init__()
        self._substring = substring

    async def gate(self, **kwargs) -> GateDecision:
        decision = await super().gate(**kwargs)
        if self._substring in kwargs["content"]:
            return GateDecision(verdict=GateVerdict.BLOCKED, content="")
        return decision


async def test_every_audit_write_is_gated_before_it_lands(repository, server, tmp_path):
    remote = repository[0]
    gate = OrderedGate(server)
    audit, executor, fake, *_ = await build_native_audit(
        repository, server, tmp_path, gate=gate
    )
    fake.issues[ROOT].status = "In Progress"
    fake.issues[ROOT].status_type = "started"
    executor.claim_verdicts = {CHILD: "refuted"}
    executor.instruction = True
    prefixes = audit_prefixes(remote.as_uri())

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN

    # Every marked comment the lane wrote passed the gate, with exactly its
    # own bytes, at or before the call that wrote it.
    marked = [
        (index, arguments["body"])
        for index, (name, arguments) in enumerate(fake.calls)
        if name == "save_comment"
        and str(arguments.get("body", "")).startswith(prefixes)
    ]
    # One escalation, the criterion's seven publications (the current-Check
    # claim, the forge report, four standing readings and the removal
    # reading) and the scope summary. Counted, so a write that stopped
    # reaching the gate shows up as a missing scan rather than as nothing.
    assert len(marked) == 9, marked
    for index, body in marked:
        assert any(
            content == body and at <= index for _writer, content, at in gate.marks
        ), body

    # The decision classification is gated before the label write lands.
    label_write = landed(fake, "save_issue", id=CHILD, addLabels=["needs-decision"])
    assert any(at <= label_write for _writer, _content, at in gate.marks)

    # The reopen carries no content of its own: it lands after the gate saw
    # its evidence, and it names nothing but the criterion and the state.
    refutation = next(
        body for _index, body in marked if '"detector":"current_check"' in body
    )
    gated_at = next(at for _writer, content, at in gate.marks if content == refutation)
    move = landed(fake, "save_issue", id=CHILD, state=unstarted_state(fake))
    assert gated_at <= move
    assert state_writes(fake) == [{"id": CHILD, "state": unstarted_state(fake)}]
    assert set(fake.calls[move][1]) == {"id", "state"}


async def test_a_blocked_refutation_leaves_the_criterion_done(
    repository, server, tmp_path
):
    audit, executor, fake, *_ = await build_native_audit(
        repository, server, tmp_path, gate=BlockingGate('"verdict":"refuted"')
    )
    fake.issues[ROOT].status = "In Progress"
    fake.issues[ROOT].status_type = "started"
    executor.claim_verdicts = {CHILD: "refuted"}

    with pytest.raises(AuditRunIncompleteError) as raised:
        await audit.run(FIXTURE_NOW)

    scope = raised.value.report.scopes[0]
    assert any("blocked" in row.reason.lower() for row in scope.unavailable), (
        scope.model_dump_json()
    )
    assert state_writes(fake) == []
    assert fake.issues[CHILD].status == "Done"
