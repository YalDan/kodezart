"""Nothing under ``src/`` calls a tool the live server does not have (KOD-144).

The roster below is the vendor server's whole advertised tool list, probed
on 2026-08-25 with the operation's own credential: sixty names, and
``list_issue_history`` is not among them.  That absence is the measured
ground of the founder's KOD-144 ruling — the provenance read called a tool
that does not exist, so no candidate could ever have cleared clause 2
against the real workspace, while the conformance suite stayed green
because the fake implemented the tool the vendor lacks.

A green suite over a fake is exactly what let a dead tool call ship, so
this module asserts against the roster rather than against any double.
The roster is a capture: it changes when the server does, and only a fresh
probe may change it.
"""

import re
from pathlib import Path

#: Every tool the live server advertises. Measured, not documented.
LIVE_TOOL_ROSTER: frozenset[str] = frozenset(
    {
        "create_attachment",
        "create_attachment_from_upload",
        "create_initiative_label",
        "create_issue_label",
        "delete_attachment",
        "delete_comment",
        "delete_diff_comment",
        "delete_status_update",
        "extract_images",
        "get_agent_skill",
        "get_attachment",
        "get_diff",
        "get_diff_threads",
        "get_document",
        "get_initiative",
        "get_issue",
        "get_issue_status",
        "get_milestone",
        "get_project",
        "get_release",
        "get_release_note",
        "get_status_updates",
        "get_team",
        "get_user",
        "get_workspace",
        "list_agent_skills",
        "list_comments",
        "list_cycles",
        "list_diffs",
        "list_documents",
        "list_initiative_labels",
        "list_initiatives",
        "list_issue_labels",
        "list_issue_statuses",
        "list_issues",
        "list_milestones",
        "list_project_labels",
        "list_projects",
        "list_release_notes",
        "list_release_pipelines",
        "list_releases",
        "list_teams",
        "list_users",
        "merge_diff",
        "prepare_attachment_upload",
        "resolve_diff_thread",
        "save_comment",
        "save_diff_comment",
        "save_document",
        "save_initiative",
        "save_issue",
        "save_milestone",
        "save_project",
        "save_release",
        "save_release_note",
        "save_status_update",
        "search_documentation",
        "share_issue",
        "submit_diff_review",
        "unshare_issue",
    },
)

ROSTER_SIZE = 60

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "kodezart"

_TOOL_CONSTANT = re.compile(
    r"^_TOOL_[A-Z0-9_]+(?::[^=]+)? = \"(?P<name>[^\"]+)\"", re.M
)
_LITERAL_CALL = re.compile(r"_call\(\s*\"")


def named_tools() -> dict[str, str]:
    """Every ``_TOOL_*`` constant under ``src/``, by the tool it names."""
    found: dict[str, str] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        for match in _TOOL_CONSTANT.finditer(path.read_text()):
            found[match.group("name")] = path.name
    return found


def test_the_roster_is_the_measured_one() -> None:
    """A guard on the fixture itself: sixty names came back from the probe."""
    assert len(LIVE_TOOL_ROSTER) == ROSTER_SIZE


def test_the_server_offers_no_issue_history_tool() -> None:
    """The measured absence KOD-144's ruling rests on."""
    assert "list_issue_history" not in LIVE_TOOL_ROSTER


#: Modules whose ``_TOOL_*`` constants address the KNOWLEDGE vendor's MCP
#: server, not the tracker's. Named per module rather than inferred, so a
#: tracker tool cannot hide in one: the paired test below pins their
#: naming shape, and their live roster is measured by the verification
#: boot (KOD-170) the way this file's Linear roster was.
KNOWLEDGE_TOOL_MODULES = frozenset({"notion_record_sink.py"})


def test_every_tracker_tool_this_process_names_exists_on_the_server() -> None:
    named = named_tools()
    assert named, "no _TOOL_* constant was found under src/"
    absent = {
        tool: module
        for tool, module in named.items()
        if module not in KNOWLEDGE_TOOL_MODULES and tool not in LIVE_TOOL_ROSTER
    }
    assert absent == {}


def test_knowledge_modules_name_only_knowledge_shaped_tools() -> None:
    """The exemption cannot shelter a tracker tool.

    The knowledge vendor's MCP tools are OpenAPI-derived and all carry the
    ``API-`` prefix; a bare tracker-vocabulary name in an exempted module
    would pass the roster test above by exemption alone, so its shape is
    pinned here.
    """
    named = named_tools()
    offenders = {
        tool: module
        for tool, module in named.items()
        if module in KNOWLEDGE_TOOL_MODULES and not tool.startswith("API-")
    }
    assert offenders == {}


def test_no_tool_is_called_by_a_bare_literal() -> None:
    """Every call names a constant, so the assertion above sees them all.

    A tool name inlined at a call site would be invisible to the roster
    check, which is how a dead call survives a green suite.
    """
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _LITERAL_CALL.search(line)
    ]
    assert offenders == []
