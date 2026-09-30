"""Every audit write reaches the sanitization gate before it reaches the backend.

Asserted at the two logs — the gate's own record and the workspace's tool
call log — and never on the text of a report. The scan derives the comments
it covers instead of naming them: every comment written during the tick must
either carry bytes the gate already saw, or be an ownership record the lease
writer parks, recognised by the shape the adapter writes it in. A comment
under any other marker purpose therefore fails the scan rather than slipping
past a list of purposes written out here.
"""

import pytest

from kodezart.adapters.linear.markers import LinearMarkers
from kodezart.services.audit_runtime import AuditRunIncompleteError
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.gating import (
    GateDecision,
    GateVerdict,
    OutboundDestination,
)
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


def parks_ownership(repo_url, body):
    """Whether *body* is the ownership record a lease writer parks.

    Read off the record's own shape, the one the adapter writes leases and
    claims in, so nothing here has to know which purposes this lane writes
    comments under.
    """
    markers = LinearMarkers(native_operation(repo_url).marker_prefixes)
    match = markers.grant_pattern.match(body)
    return match is not None and "kind: lease" in match.group("payload")


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
    repo_url = remote.as_uri()
    before = len(fake.calls)

    assert await audit.run(FIXTURE_NOW) is PassRun.RAN

    # Every comment this tick wrote is either an ownership record the lease
    # writer parks or a body the gate saw, with exactly those bytes, at or
    # before the call that wrote it. Nothing else is allowed through.
    gated = []
    for index, (name, arguments) in enumerate(fake.calls):
        if index < before or name != "save_comment":
            continue
        body = str(arguments.get("body", ""))
        if parks_ownership(repo_url, body):
            continue
        assert any(
            content == body and at <= index for _writer, content, at in gate.marks
        ), body
        gated.append(body)
    # One escalation, the criterion's seven publications (the current-Check
    # claim, the forge report, four standing readings and the removal
    # reading) and the scope summary. Counted, so a write that stopped
    # reaching the gate shows up as a missing scan rather than as nothing.
    assert len(gated) == 9, gated

    # The decision classification itself is gated, at its own destination and
    # with its own content, before the label write lands.
    label_write = landed(fake, "save_issue", id=CHILD, addLabels=["needs-decision"])
    assert any(
        writer == OutboundDestination.TRACKER_CLASSIFICATION.value
        and content == "decision"
        and at <= label_write
        for writer, content, at in gate.marks
    ), gate.marks

    # The reopen carries no content of its own: it lands after the gate saw
    # its evidence, and it names nothing but the criterion and the state.
    refutation = next(body for body in gated if '"detector":"current_check"' in body)
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
