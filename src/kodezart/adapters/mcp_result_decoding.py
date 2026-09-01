"""Decoding one MCP tool result, shared by every transport.

What a server may answer with is a property of the PROTOCOL, not of the
wire it arrived on: an HTTP session and a spawned stdio process deliver
the same ``CallToolResult`` shape, and duplicating the decoding per
transport is two places for the KOD-143 lessons to drift apart.  The
transports differ only in how a session is opened, which stays in their
own modules.
"""

import json

from mcp.types import CallToolResult, TextContent

from kodezart.core.errors import McpTransportError
from kodezart.core.protocols import McpToolResult


def error_detail(result: CallToolResult, *, limit: int) -> str:
    """The server's OWN words about the refusal, bounded.

    An error result carries the vendor's diagnosis in its content blocks —
    the field that was wrong, the type it wanted, the status it answered.
    Dropping it and raising a bare "the server reported a tool error"
    leaves a caller knowing only that something failed, which cost a whole
    boot cycle to recover once (KOD-143): the server had said "teamId must
    be a UUID" and nothing carried it.
    """
    text = " ".join(
        block.text for block in result.content if isinstance(block, TextContent)
    ).strip()
    if not text:
        return "the server sent no readable diagnosis"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def structured_result(
    result: CallToolResult,
    *,
    server_name: str,
    tool_name: str,
) -> McpToolResult:
    """The structured result from either source, in order.

    ``structuredContent`` when present; otherwise a single text-content
    block whose text parses as a JSON object OR a JSON array — the spec
    makes the first optional and the vendor's live server sends only the
    second.  An array is a shape a tool really answers with
    (``list_issue_statuses``, measured under KOD-143), so refusing one
    here would make that tool unreachable from every adapter.  Every
    other shape is a refusal naming exactly what was absent or
    undecodable, never a guessed-at result.
    """
    structured = result.structuredContent
    if structured is not None:
        return structured
    blocks = result.content
    if not blocks:
        raise McpTransportError(
            "the MCP server returned no structured content and no content blocks",
            server_name=server_name,
            tool_name=tool_name,
        )
    if len(blocks) > 1:
        raise McpTransportError(
            "the MCP server returned several content blocks where one "
            "structured result was expected",
            server_name=server_name,
            tool_name=tool_name,
        )
    block = blocks[0]
    if not isinstance(block, TextContent):
        raise McpTransportError(
            "the MCP server's single content block is not text",
            server_name=server_name,
            tool_name=tool_name,
        )
    try:
        parsed: object = json.loads(block.text)
    except json.JSONDecodeError as exc:
        raise McpTransportError(
            "the MCP server's text content is not valid JSON",
            server_name=server_name,
            tool_name=tool_name,
        ) from exc
    if not isinstance(parsed, dict | list):
        raise McpTransportError(
            "the MCP server's text content is JSON but neither an object nor an array",
            server_name=server_name,
            tool_name=tool_name,
        )
    return parsed
