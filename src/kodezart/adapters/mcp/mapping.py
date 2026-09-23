"""Mapping the MCP servers kodezart attaches onto a session — options and prompt.

One helper serves both executor option-construction sites, so which servers
a session kind receives is decided in exactly one expression.  That is what
keeps the unwired executor from becoming a hole: it is not covered by a
second copy of the rule, it is covered by the same one.

Two servers can be attached.  The knowledge server goes to every session
type the grant names.  The tracker server goes to the scheduled-pass
sessions (grooming, fire prep and the audit judges) whenever the deployment
holds a tracker credential, so those sessions work the board through the
same server definition the programmatic client dials.

The working-directory guard is NOT one of that decision's consequences.
Every session the service starts carries it, because the danger it answers
is the session's working directory — a cloned repository for the fire
types, a shared-temporary path for the audit one — and not whether this
process happened to describe a server.  A session with nothing described
therefore runs strict with an empty server map: no MCP at all.

The grant has two consequences — the knowledge server a session is
configured with, and the what-lives-where map its prompt is preluded with.
The second reads the RESULT of the first rather than re-testing membership,
so the two can never be answered differently for one session.
"""

from typing import TypedDict

from claude_agent_sdk.types import (
    McpServerConfig,
)

from kodezart.core.prompt_rendering import PromptTemplate
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import (
    HttpKnowledge,
    HttpMcpServer,
    KnowledgeGrant,
    SessionType,
    StdioKnowledge,
)

#: The session kind the tracker server is attached to: the scheduled passes,
#: which keep the board in order through it.  No other kind receives it.
TRACKER_SESSION: SessionType = SessionType.SCHEDULED_PASS


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


def _tracker_server(
    tracker: HttpMcpServer | None,
    session_type: SessionType,
) -> dict[str, McpServerConfig]:
    """The tracker server for *session_type*, or nothing.

    Nothing without a tracker definition — a deployment holding no tracker
    credential — and nothing for a session kind other than the scheduled
    passes.  Dispatched with ``if`` for the same reason as the knowledge
    routes above.
    """
    if tracker is None or session_type is not TRACKER_SESSION:
        return {}
    return {
        tracker.name: {
            "type": "http",
            "url": tracker.url,
            "headers": dict(tracker.headers),
        }
    }


def map_knowledge_mcp(
    grant: KnowledgeGrant,
    session_type: SessionType,
    tracker: HttpMcpServer | None = None,
) -> McpSessionOptions:
    """Session options for *session_type* under *grant* and *tracker*.

    The one mapping of servers per session kind: the knowledge server per
    the grant, and the tracker server to the scheduled passes when a tracker
    definition is present.  A tracker server named like the knowledge server
    refuses rather than replacing it.  Boot refuses that clash first, naming
    both servers; this refusal is the last line of defence, for a caller
    that builds an executor without booting.

    Exhaustive over the vocabulary with no default arm, so a session kind
    added later fails to type-check rather than reaching the SDK default
    and running its working directory unguarded.
    """
    knowledge = _described_servers(grant, session_type)
    board = _tracker_server(tracker, session_type)
    if knowledge.keys() & board.keys():
        msg = (
            f"the tracker server and the knowledge server are both named "
            f"{grant.server_name!r}; one session cannot be given both"
        )
        raise ValueError(msg)
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
                mcp_servers={**knowledge, **board},
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
    :func:`map_knowledge_mcp` answered for this session: no knowledge
    server among the attached ones means the grant does not name it, and
    its prompt is returned unchanged — byte for byte the string the caller
    passed.  A session told what lives where is therefore exactly a session
    configured to reach it.

    The gate reads the knowledge server's own entry in the server map rather
    than the map as a whole: the tracker server can be attached to a session
    the grant does not name, and the mapping always carries the
    working-directory guard, so neither's presence answers this question.
    """
    if grant.server_name not in attached["mcp_servers"]:
        return prompt
    if fire_record is not None and run_identity is not None:
        clause = fire_record.render({"record_title": run_identity.title()})
        return f"{grant.knowledge_map}\n\n{prompt}\n\n{clause}"
    return f"{grant.knowledge_map}\n\n{prompt}"
