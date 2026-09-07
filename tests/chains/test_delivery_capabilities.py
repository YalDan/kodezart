"""Delivery can create an open PR; merge and issue mutation are not capabilities."""

import ast
import inspect

from kodezart.chains.delivery_coordinator import DeliveryCoordinator
from kodezart.core.protocols import ForgeQuery, PRContentEditor, PRCreator
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.pr_content import PRContent
from tests.chains.test_delivery_runtime import BASE, HEAD, REPOSITORY, deliver, setup
from tests.fakes import FakePRCreator


def test_pr_creator_and_forge_double_expose_exactly_the_two_write_methods():
    for owner in (PRCreator, FakePRCreator):
        assert {
            name
            for name, member in vars(owner).items()
            if callable(member) and not name.startswith("_")
        } == {"create_pr", "comment_on_pr"}


def test_coordinator_has_no_merge_or_issue_mutation_dependency():
    constructor = inspect.signature(DeliveryCoordinator)
    assert constructor.parameters["pr_creator"].annotation is PRCreator
    assert constructor.parameters["forge_query"].annotation is ForgeQuery
    assert constructor.parameters["pr_editor"].annotation is PRContentEditor
    assert "tracker" not in constructor.parameters
    tree = ast.parse(inspect.getsource(DeliveryCoordinator))
    creator_reads = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
        and node.value.attr == "_pr_creator"
    }
    assert creator_reads == {"create_pr"}
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"merged", "merged_at", "mergeable", "merge", "merge_pr"}
        for node in ast.walk(tree)
    )


def test_content_editor_exposes_only_content_and_open_identity():
    assert {
        name
        for name, member in vars(PRContentEditor).items()
        if callable(member) and not name.startswith("_")
    } == {"read_open_pr", "edit_pr"}
    assert set(PRContent.model_fields) == {
        "url",
        "number",
        "head_branch",
        "base_branch",
        "title",
        "body",
    }


async def test_green_lane_creates_and_watches_once_then_returns_open():
    fixture = setup()
    result = await deliver(fixture.coordinator)
    assert [call["method"] for call in fixture.forge.calls] == ["create_pr"]
    assert fixture.forge.calls[0]["head"] == HEAD
    assert fixture.forge.calls[0]["base"] == BASE
    assert fixture.monitor.calls == [{"repo_url": REPOSITORY, "ref": HEAD}]
    assert fixture.monitor.declaration_calls == fixture.monitor.rerun_calls == []
    assert result.outcome is WorkflowOutcome.ci_passed
    assert result.pr.state == "open"
