"""Native container ancestry participates in the actual FireSpec reader."""

import pytest

from kodezart.domain.errors import FireSpecEntryError
from kodezart.types.domain.operation import ScopeLabel
from tests.tracker.conftest import FIRE_STAGE_KEY, FIRE_STAGE_LABEL
from tests.tracker.test_scope_approval import CHILD, INITIATIVE, OTHER
from tests.tracker.test_scope_approval import approval as approval
from tests.tracker.test_scope_reads import ScopeMcpIssue


@pytest.mark.parametrize("ancestor", [OTHER, INITIATIVE])
async def test_container_approval_and_revocation_reach_actual_spec_reader(
    approval, ancestor
):
    criterion = "criterion/under-container"
    approval.server.issues[CHILD.key].labels = [FIRE_STAGE_LABEL]
    approval.server.issues[criterion] = ScopeMcpIssue(
        id=criterion,
        parent_id=CHILD.key,
        labels=["acceptance-condition"],
        description="**Check:** Native container approval is observable.",
    )
    child = approval.fake.issues[CHILD.key]
    approval.fake.criteria_stage_label_key = FIRE_STAGE_KEY
    approval.fake.issues[CHILD.key] = child.model_copy(
        update={"issue_labels": frozenset({FIRE_STAGE_KEY})}
    )
    approval.fake.issues[criterion] = child.model_copy(
        update={
            "issue_key": criterion,
            "parent_key": CHILD.key,
            "issue_labels": frozenset({"criterion"}),
            "body": "**Check:** Native container approval is observable.",
        }
    )
    approval.labels(ancestor, ScopeLabel.APPROVED)
    spec = await approval.tracker.read_fire_spec(issue_key=CHILD.key)
    assert spec.subject == CHILD.key
    assert spec.criteria == (criterion,)
    approval.labels(ancestor)
    with pytest.raises(FireSpecEntryError, match="approval"):
        await approval.tracker.read_fire_spec(issue_key=CHILD.key)
    assert not approval.fake.issue_writes
    assert not approval.fake.comment_writes
    assert not approval.fake.claim_writes
    assert not approval.server.tool_calls("save_issue")
