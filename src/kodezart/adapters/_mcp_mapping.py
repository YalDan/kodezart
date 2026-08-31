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

from typing import Final, TypedDict

from claude_agent_sdk.types import (
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
)

from kodezart.types.domain.session import (
    KnowledgeGrant,
    KnowledgeTransport,
    SessionType,
)

#: The header and scheme a client presents a gateway credential in.  This is
#: the documented self-hosted HTTP shape — the server sits behind a bearer
#: gateway token — and it is fixed so the upstream pass-through header can
#: never silently collide with it.
_GATEWAY_HEADER: Final[str] = "Authorization"
_GATEWAY_SCHEME: Final[str] = "Bearer"


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
    if grant.transport is KnowledgeTransport.STDIO:
        return {grant.server_name: _stdio_definition(grant, session_type)}
    return {grant.server_name: _http_definition(grant, session_type)}


def _http_definition(
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> McpHttpServerConfig:
    """The HTTP server definition a granted session dials.

    Three expressible header shapes: the upstream credential alone in its
    configured header, the gateway credential alone as a bearer, or both at
    once — the vendor's token pass-through, where the upstream header must
    differ from the gateway's.  No headers at all is the dead configuration
    and refuses rather than dialling unauthenticated.
    """
    headers: dict[str, str] = {}
    if grant.gateway_credential is not None:
        headers[_GATEWAY_HEADER] = (
            f"{_GATEWAY_SCHEME} {grant.gateway_credential.get_secret_value()}"
        )
    if grant.credential is not None:
        if grant.auth_header is None:
            msg = (
                f"knowledge grant carries a credential but no auth_header to "
                f"present it in: set KODEZART_KNOWLEDGE_MCP_AUTH_HEADER, or "
                f"unset the credential ({grant.server_name})"
            )
            raise ValueError(msg)
        if (
            grant.auth_header == _GATEWAY_HEADER
            and grant.gateway_credential is not None
        ):
            msg = (
                f"knowledge grant presents both credentials in "
                f"{_GATEWAY_HEADER!r}: the gateway credential owns that "
                f"header, so KODEZART_KNOWLEDGE_MCP_AUTH_HEADER must name the "
                f"pass-through header the self-hosted server documents"
            )
            raise ValueError(msg)
        composed = (
            grant.credential.get_secret_value()
            if grant.auth_scheme is None
            else f"{grant.auth_scheme} {grant.credential.get_secret_value()}"
        )
        headers[grant.auth_header] = composed
    if not headers:
        msg = (
            f"knowledge grant names {session_type.value} but carries no "
            f"credential: {grant.server_name} would be dialled unauthenticated"
        )
        raise ValueError(msg)
    if grant.server_url is None:
        msg = (
            f"knowledge grant carries no server_url for its http transport: "
            f"{grant.server_name} has no endpoint to dial"
        )
        raise ValueError(msg)
    return {
        "type": "http",
        "url": grant.server_url,
        "headers": headers,
    }


def _stdio_definition(
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> McpStdioServerConfig:
    """The stdio server definition a granted session spawns.

    The credential is delivered as one environment entry of the spawned
    process, under the name the server documents.  There is no URL and
    there are no headers — that absence is the shape, not a gap in it.
    """
    if grant.command is None:
        msg = (
            f"knowledge grant carries no command for its stdio transport: "
            f"{grant.server_name} has no process to spawn"
        )
        raise ValueError(msg)
    if grant.credential is None:
        msg = (
            f"knowledge grant names {session_type.value} but carries no "
            f"credential: {grant.server_name} would be spawned unauthenticated"
        )
        raise ValueError(msg)
    if grant.credential_env is None:
        msg = (
            f"knowledge grant carries a credential but no credential_env "
            f"entry to deliver it under: set "
            f"KODEZART_KNOWLEDGE_MCP_CREDENTIAL_ENV to the environment "
            f"variable {grant.server_name} reads its token from"
        )
        raise ValueError(msg)
    return {
        "type": "stdio",
        "command": grant.command,
        "args": list(grant.args),
        "env": {**grant.env, grant.credential_env: grant.credential.get_secret_value()},
    }


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
    return f"{grant.knowledge_map}\n\n{prompt}"
