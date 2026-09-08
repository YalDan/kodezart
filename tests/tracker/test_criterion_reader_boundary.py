"""Incomplete vendor reads never masquerade as a parent with no criteria."""

from collections.abc import Mapping

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.domain.errors import CriterionReadError
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.test_linear_mcp_tracker import tracker_over

PARENT = "PARENT/1"
CHILD = "CHILD/1"
LABEL = "acceptance-condition"


class ChildPagesServer(FakeLinearMcpServer):
    def __init__(self, *, pages, children=()):
        super().__init__(issues=[FakeMcpIssue(id=PARENT), *children])
        self.pages = pages

    def _tool_list_issues(self, arguments: Mapping[str, object]):
        return self.pages[arguments.get("cursor")]


async def test_later_pages_are_full_reads_and_page_overlap_is_one_object():
    child = FakeMcpIssue(
        id=CHILD,
        parent_id=PARENT,
        labels=[LABEL],
        description="long description " * 500 + "**Evidence:** full tail",
    )
    server = ChildPagesServer(
        children=[child],
        pages={
            None: {"issues": [], "hasNextPage": True, "cursor": "next"},
            "next": {
                "issues": [{"id": CHILD, "description": "truncated"}],
                "hasNextPage": True,
                "cursor": "last",
            },
            "last": {"issues": [{"id": CHILD}], "hasNextPage": False},
        },
    )
    criteria = await tracker_over(server).read_criteria(issue_key=PARENT)
    assert len(criteria) == 1
    assert criteria[0].body == child.description
    assert server.tool_calls("save_issue") == []
    assert all(
        call["parentId"] == PARENT and call["includeArchived"] is True
        for call in server.tool_calls("list_issues")
    )


@pytest.mark.parametrize("cursor", [None, "next"])
async def test_incomplete_pagination_is_a_failed_read(cursor):
    server = ChildPagesServer(
        pages={
            None: {"issues": [], "hasNextPage": True, "cursor": "next"},
            "next": {"issues": [], "hasNextPage": True, "cursor": cursor},
        }
    )
    with pytest.raises(CriterionReadError, match="pagination"):
        await tracker_over(server).read_criteria(issue_key=PARENT)


async def test_a_child_moved_during_listing_cannot_supply_a_complete_answer():
    server = ChildPagesServer(
        children=[FakeMcpIssue(id=CHILD, parent_id="another/1", labels=[LABEL])],
        pages={None: {"issues": [{"id": CHILD}], "hasNextPage": False}},
    )
    with pytest.raises(CriterionReadError, match="current identity or parent"):
        await tracker_over(server).read_criteria(issue_key=PARENT)


async def test_missing_configuration_is_not_an_empty_criterion_set():
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id=PARENT)])
    with pytest.raises(OperationMemberAbsentError, match="issue_labels"):
        await tracker_over(server, issue_labels={}).read_criteria(issue_key=PARENT)
    assert server.calls == []


async def test_failed_listing_retains_the_typed_read_boundary():
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id=PARENT)], tool_errors={"list_issues": "unavailable"}
    )
    with pytest.raises(CriterionReadError) as raised:
        await tracker_over(server).read_criteria(issue_key=PARENT)
    assert raised.value.issue_key == PARENT
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("field", ["labels", "parentId"])
async def test_missing_child_membership_fields_are_not_defaulted(field):
    class IncompleteChildServer(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == CHILD:
                del payload[field]
            return payload

    server = IncompleteChildServer(
        issues=[
            FakeMcpIssue(id=PARENT),
            FakeMcpIssue(id=CHILD, parent_id=PARENT, labels=[LABEL]),
        ]
    )
    with pytest.raises(CriterionReadError):
        await tracker_over(server).read_criteria(issue_key=PARENT)


@pytest.mark.parametrize("description", [None, "", "omitted"])
async def test_descriptionless_criteria_keep_the_ordinary_issue_read_contract(
    description,
):
    class DescriptionlessServer(FakeLinearMcpServer):
        def _tool_get_issue(self, arguments):
            payload = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == CHILD:
                if description == "omitted":
                    del payload["description"]
                else:
                    payload["description"] = description
            return payload

    server = DescriptionlessServer(
        issues=[
            FakeMcpIssue(id=PARENT),
            FakeMcpIssue(id=CHILD, parent_id=PARENT, labels=[LABEL]),
        ]
    )
    tracker = tracker_over(server)
    criteria = await tracker.read_criteria(issue_key=PARENT)
    assert len(criteria) == 1
    assert criteria[0].body == ""
    assert criteria[0] == await tracker.read_issue(issue_key=CHILD)


async def test_composition_injects_the_declared_criterion_label_spelling():
    label = "Verification requirement"
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        issue_labels={"criterion": label},
    )
    server = FakeLinearMcpServer(
        issues=[
            FakeMcpIssue(id=PARENT),
            FakeMcpIssue(id=CHILD, parent_id=PARENT, labels=[label]),
            FakeMcpIssue(id="OLD/1", parent_id=PARENT, labels=[LABEL, "criterion"]),
        ]
    )
    config = AppConfig()
    tracker, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=operation,
        caller=server,
    )
    assert [
        criterion.issue_key
        for criterion in await tracker.read_criteria(issue_key=PARENT)
    ] == [CHILD]
