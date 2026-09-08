"""Measured document references omit URL; only the asset reader hydrates it."""

import asyncio

import pytest

from kodezart.core.errors import McpTransportError, TrackerProtocolError
from tests.fakes import FakeMcpDocument, FakeMcpIssue
from tests.model_members import model_workspace

DOCUMENT = {"id": "document/native", "title": "An attached design"}
URL = "https://tracker.invalid/document/server-assigned-slug"


@pytest.fixture
async def native_document(monkeypatch):
    workspace = await model_workspace("native")
    await workspace.put(FakeMcpIssue(id="subject", description="Unchanged body"))
    workspace.server.documents[DOCUMENT["id"]] = FakeMcpDocument(
        **DOCUMENT, content="Document content"
    )
    original = workspace.server.call_tool
    shape = {"relation": dict(DOCUMENT), "document": {**DOCUMENT, "url": URL}}

    async def measured(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "get_issue":
            result["documents"] = [shape["relation"]]
        elif name == "get_document":
            result.update(shape["document"])
        return result

    monkeypatch.setattr(workspace.server, "call_tool", measured)
    workspace.server.calls.clear()
    return workspace, shape


@pytest.mark.parametrize("reader", ["read_issue", "read_planning_issue"])
async def test_full_issue_read_accepts_measured_document_reference(
    native_document, reader
):
    workspace, _ = native_document
    issue = await getattr(workspace.native, reader)(issue_key="subject")
    assert issue.body == "Unchanged body"
    assert [name for name, _ in workspace.server.calls] == ["get_issue"]


async def test_asset_read_hydrates_exact_native_document_and_preserves_row(
    native_document,
):
    workspace, _ = native_document
    assets = await workspace.native.list_issue_assets(issue_key="subject")
    assert len(assets) == 1
    assert assets[0].asset_key == DOCUMENT["id"]
    assert assets[0].title == DOCUMENT["title"]
    assert assets[0].url == URL
    assert workspace.server.calls[-1] == ("get_document", {"id": DOCUMENT["id"]})
    assert (
        await workspace.native.read_document(document_key=DOCUMENT["id"])
        == "Document content"
    )


async def test_already_reported_document_url_needs_no_extra_read(native_document):
    workspace, shape = native_document
    shape["relation"]["url"] = URL
    assert (await workspace.native.list_issue_assets(issue_key="subject"))[0].url == URL
    assert [name for name, _ in workspace.server.calls] == ["get_issue"]


@pytest.mark.parametrize("field", ["id", "title", "url"])
@pytest.mark.parametrize("value", [None, 42])
async def test_unreadable_hydrated_metadata_never_invents_an_asset(
    native_document, field, value
):
    workspace, shape = native_document
    shape["document"][field] = value
    with pytest.raises(TrackerProtocolError):
        await workspace.native.list_issue_assets(issue_key="subject")


@pytest.mark.parametrize("field", ["id", "title"])
async def test_changed_document_identity_or_title_refuses(native_document, field):
    workspace, shape = native_document
    shape["document"][field] = "a different document"
    with pytest.raises(TrackerProtocolError):
        await workspace.native.list_issue_assets(issue_key="subject")


async def test_missing_attachment_url_still_refuses(native_document, monkeypatch):
    workspace, _ = native_document
    original = workspace.server.call_tool

    async def attachment(*, name, arguments):
        result = await original(name=name, arguments=arguments)
        if name == "get_issue":
            result["attachments"] = [dict(DOCUMENT)]
        return result

    monkeypatch.setattr(workspace.server, "call_tool", attachment)
    with pytest.raises(TrackerProtocolError):
        await workspace.native.read_issue(issue_key="subject")


@pytest.mark.parametrize("cancel", [False, True])
async def test_document_read_failure_and_cancellation_propagate(
    native_document, monkeypatch, cancel
):
    workspace, _ = native_document
    original = workspace.server.call_tool

    async def failed(*, name, arguments):
        if name == "get_document":
            if cancel:
                raise asyncio.CancelledError
            raise McpTransportError(
                "unavailable", server_name="fixture", tool_name=name
            )
        return await original(name=name, arguments=arguments)

    monkeypatch.setattr(workspace.server, "call_tool", failed)
    with pytest.raises(asyncio.CancelledError if cancel else McpTransportError):
        await workspace.native.list_issue_assets(issue_key="subject")
