"""A root issue's ``get_issue`` answer carries no ``parentId`` key; that is no parent.

Measured 2026-09-24 on the first live approval read of a scope member: the
connected Linear MCP answers ``get_issue`` for a root issue without the key
at all, and for a sub-issue with its parent's key.  The approval read used to
require the key, so every root member of a scope refused with
"tracker response does not match its declared shape" before any label was
read.
"""

from collections.abc import Mapping

from kodezart.core.protocols import McpToolResult
from kodezart.types.domain.operation import ScopeLabel
from tests.tracker.conftest import linear_over_fake_mcp
from tests.tracker.test_scope_reads import ROOT, ScopeMcpServer

SCOPE_LABELS = {member.value: f"scope:{member.value}" for member in ScopeLabel}


class _RootWithoutTheKey(ScopeMcpServer):
    """The fixture, answering the root's detail read without a ``parentId`` key."""

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        payload = await super().call_tool(name=name, arguments=arguments)
        if name == "get_issue" and arguments.get("id") == ROOT.key:
            assert isinstance(payload, Mapping)
            return {key: value for key, value in payload.items() if key != "parentId"}
        return payload


async def test_a_root_answer_without_the_key_reads_as_no_parent() -> None:
    server = _RootWithoutTheKey()
    server.issues[ROOT.key].labels = [SCOPE_LABELS[ScopeLabel.APPROVED.value]]
    tracker = linear_over_fake_mcp(server, scope_labels=SCOPE_LABELS)

    members = await tracker.read_scope_labels(ref=ROOT)

    assert members == frozenset({ScopeLabel.APPROVED})


async def test_a_sub_issue_answer_still_carries_its_parent() -> None:
    server = _RootWithoutTheKey()
    server.issues["FIX-2"].labels = [SCOPE_LABELS[ScopeLabel.TRIAGE.value]]
    tracker = linear_over_fake_mcp(server, scope_labels=SCOPE_LABELS)

    members = await tracker.read_scope_labels(
        ref=ROOT.model_copy(update={"key": "FIX-2"})
    )

    assert members == frozenset({ScopeLabel.TRIAGE})
    issues = await tracker.scope_issues(ref=ROOT)
    assert next(i.parent_key for i in issues if i.issue_key == "FIX-2") == ROOT.key
