"""Delivery wire consumers receive one shared PR and an explicit check tri-state."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.delivery import LaneDelivery
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR


@pytest.mark.parametrize("checks", [True, False, None])
def test_wire_roundtrip_preserves_check_state_and_dispatch_base(checks):
    pr = LanePR(url="https://example.invalid/pr/7", number=7, state="open")
    delivery = LaneDelivery(
        lane_key="lane",
        issue_id="issue",
        head_branch="feature",
        base_branch="blocker-branch",
        pr=pr,
        checks_passed=checks,
        checks_summary=None,
        outcome=WorkflowOutcome.pr_opened,
    )
    assert delivery.pr is pr
    wire = delivery.model_dump(mode="json", by_alias=True)
    assert wire["baseBranch"] == "blocker-branch"
    assert "checksPassed" in wire and wire["checksPassed"] is checks
    assert LaneDelivery.model_validate(wire) == delivery
    with pytest.raises(ValidationError, match="frozen"):
        delivery.base_branch = "trunk"
    with pytest.raises(ValidationError, match="frozen"):
        pr.state = "changed"


def test_absent_pr_is_explicit_and_base_is_not_defaulted():
    fields = {
        "lane_key": "lane",
        "issue_id": "issue",
        "head_branch": "feature",
        "pr": None,
        "checks_passed": None,
        "checks_summary": None,
        "outcome": WorkflowOutcome.review_passed_no_pr_adapter,
    }
    with pytest.raises(ValidationError, match="baseBranch"):
        LaneDelivery(**fields)
    delivery = LaneDelivery(**fields, base_branch="resolved")
    assert delivery.model_dump()["pr"] is None


def test_forge_exemption_is_declared_and_defaults_false():
    fields = {"url": "https://example.invalid/repository", "trunk": "integration"}
    assert RepoEntry(**fields).forge_exempt is False
    assert RepoEntry(**fields, forge_exempt=True).forge_exempt is True
