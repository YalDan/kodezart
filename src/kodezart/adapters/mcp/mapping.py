"""Mapping the resolved knowledge grant onto a session — options and prompt.

One helper serves both executor option-construction sites, so the grant
decision is taken in exactly one expression.  That is what keeps the
unwired executor from becoming a hole: it is not covered by a second copy
of the rule, it is covered by the same one.

The working-directory guard is NOT one of that decision's consequences.
Every session the service starts carries it, because the danger it answers
is the session's working directory — a cloned repository for the fire
types, a shared-temporary path for the audit one — and not whether this
process happened to describe a server.  A session with nothing described
therefore runs strict with an empty server map: no MCP at all.

The grant has two consequences — the servers a session is configured with,
and the what-lives-where map its prompt is preluded with.  The second reads
the RESULT of the first rather than re-testing membership, so the two can
never be answered differently for one session.
"""

from typing import TypedDict

from claude_agent_sdk.types import (
    McpServerConfig,
)

from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import (
    HttpKnowledge,
    KnowledgeGrant,
    SessionType,
    StdioKnowledge,
)


class McpSessionOptions(TypedDict):
    """The MCP option keywords every session is constructed with.

    Both keys are always present, and they answer different questions.
    ``mcp_servers`` is what this process described, possibly nothing;
    ``strict_mcp_config`` is whether definitions discovered in the
    session's working directory may load beside it, which is never.
    """

    mcp_servers: dict[str, McpServerConfig]
    strict_mcp_config: bool


def _described_servers(
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> dict[str, McpServerConfig]:
    """The servers this process describes for *session_type*.

    Empty for a session the grant does not name — the shipped grant names
    none, so this is the shipped answer for every session type.  A named
    session receives the definition its transport renders: the routes are
    dispatched with ``if`` rather than ``match`` deliberately, so the
    session-kind census below stays the module's only match statement.
    """
    if not grant.grants(session_type):
        return {}
    connection = grant.connection
    if isinstance(connection, StdioKnowledge):
        return {
            grant.server_name: {
                "type": "stdio",
                "command": connection.command,
                "args": list(connection.args),
                "env": connection.environment(),
            }
        }
    if isinstance(connection, HttpKnowledge):
        return {
            grant.server_name: {
                "type": "http",
                "url": connection.server_url,
                "headers": connection.headers(),
            }
        }
    raise ValueError("granted session has no knowledge connection")


def map_knowledge_mcp(
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> McpSessionOptions:
    """Session options for *session_type* under *grant*.

    Exhaustive over the vocabulary with no default arm, so a session kind
    added later fails to type-check rather than reaching the SDK default
    and running its working directory unguarded.
    """
    match session_type:
        case (
            SessionType.TICKET_FIRE
            | SessionType.API_QUERY
            | SessionType.COMMIT_MESSAGE
            | SessionType.CONTENT_AUDIT
            | SessionType.SCHEDULED_PASS
            | SessionType.ORGANIZE_PASS
        ):
            return McpSessionOptions(
                mcp_servers=_described_servers(grant, session_type),
                strict_mcp_config=True,
            )


def prompt_with_knowledge_map(
    prompt: str,
    *,
    grant: KnowledgeGrant,
    attached: McpSessionOptions,
    fire_record: PromptTemplate | None = None,
    run_identity: RunIdentity | None = None,
) -> str:
    """*prompt* preceded by the what-lives-where map, for a granted session.

    The grant decision is not re-taken here.  *attached* is what
    :func:`map_knowledge_mcp` answered for this session: no described
    server means the grant does not name it, and its prompt is returned
    unchanged — byte for byte the string the caller passed.  A session told
    what lives where is therefore exactly a session configured to reach it.

    The gate reads the server map rather than the mapping as a whole: the
    mapping always carries the working-directory guard, so its own
    truthiness answers a different question than this one.
    """
    if not attached["mcp_servers"]:
        return prompt
    if fire_record is not None and run_identity is not None:
        clause = fire_record.render({"record_title": run_identity.title()})
        return f"{grant.knowledge_map}\n\n{prompt}\n\n{clause}"
    return f"{grant.knowledge_map}\n\n{prompt}"
