"""The actual redacting gate cannot publish a damaged tracker identity."""

import re

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.domain.errors import PRTrackerIdentityError
from kodezart.domain.pr_body import require_tracker_issue
from kodezart.types.domain.agent import ErrorEvent
from kodezart.types.domain.gating import (
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    ScanResult,
)
from kodezart.types.requests.agent import WorkflowRequest
from tests.integration.test_issue_key_carriage import (
    DECOY_PROMPT,
    GENERATED_DESCRIPTION,
    ISSUE_KEY,
    REPO_URL,
    TRUNK,
    finish,
    workflow_harness,
)
from tests.services.test_fire_dispatcher import LANE


class ObservedScanner(RegexContentScanner[RedactionCategory]):
    def __init__(self, text):
        super().__init__(patterns={RedactionCategory.TRACKER_URLS: [re.escape(text)]})
        self.bodies = []

    async def scan(
        self, *, content: str, destination: OutboundDestination
    ) -> ScanResult:
        if destination is OutboundDestination.PR_BODY:
            self.bodies.append(content)
        return await super().scan(content=content, destination=destination)


def redacting_gate(text):
    scanner = ObservedScanner(text)
    return scanner, PatternOutboundContentGate(
        scanners=[scanner],
        verdicts={RedactionCategory.TRACKER_URLS: GateVerdict.REDACTED},
    )


@pytest.mark.parametrize("stalled", [False, True])
async def test_real_dispatch_refuses_when_actual_gate_redacts_its_issue_key(stalled):
    scanner, gate = redacting_gate(ISSUE_KEY)
    async with workflow_harness(gate, stalled=stalled) as harness:
        report = await harness.dispatcher.run_pass()
        events = await finish(harness.queue, report.job_id)
        assert harness.creator.calls == []
        assert scanner.bodies[0].endswith(f"Tracker issue: {ISSUE_KEY}")
        (error,) = [event for event in events if isinstance(event, ErrorEvent)]
        assert error.error_kind == "PRTrackerIdentityError"


@pytest.mark.parametrize(
    "body",
    [
        "No fixed line.",
        "Tracker issue: other/42",
        "Tracker issue: subject/420",
        "prefix Tracker issue: subject/42",
        "Tracker issue: subject/42 suffix",
    ],
)
def test_identity_requires_the_exact_complete_line(body):
    with pytest.raises(PRTrackerIdentityError):
        require_tracker_issue(body, "subject/42")


@pytest.mark.parametrize("suffix", ["", "\nAdditional permitted prose."])
def test_validation_preserves_all_gated_bytes(suffix):
    body = "Rewritten prose.\n\nTracker issue: subject/42" + suffix
    assert require_tracker_issue(body, "subject/42") is body


@pytest.mark.parametrize("issue_key", [None, "external/42"])
async def test_legacy_http_keeps_permitted_redaction_with_optional_identity(issue_key):
    scanner, gate = redacting_gate(GENERATED_DESCRIPTION)
    async with workflow_harness(gate) as harness:
        request = WorkflowRequest(
            prompt=DECOY_PROMPT,
            repo_url=REPO_URL,
            base_branch=TRUNK,
            issue_key=issue_key,
        )
        record = await harness.handler.submit_workflow(request, lane=LANE)
        events = await finish(harness.queue, record.job_id)
        assert not [event for event in events if isinstance(event, ErrorEvent)]
        (created,) = harness.creator.calls
        assert GENERATED_DESCRIPTION in scanner.bodies[0]
        assert GENERATED_DESCRIPTION not in created["body"]
        assert "[REDACTED:tracker_urls]" in created["body"]
        if issue_key is None:
            assert "Tracker issue:" not in created["body"]
        else:
            assert created["body"].endswith(f"Tracker issue: {issue_key}")
