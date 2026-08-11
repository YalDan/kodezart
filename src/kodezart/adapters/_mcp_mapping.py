"""Mapping the resolved knowledge grant onto a session — options and prompt.

One helper serves both executor option-construction sites, so the grant
decision is taken in exactly one expression.  That is what keeps the
unwired executor from becoming a hole: it is not covered by a second copy
of the rule, it is covered by the same one.

The grant has two consequences — the servers a session is configured with,
and the what-lives-where map its prompt is preluded with.  The second reads
the RESULT of the first rather than re-testing membership, so the two can
never be answered differently for one session.
"""

from typing import TypedDict

from claude_agent_sdk.types import McpHttpServerConfig, McpServerConfig

from kodezart.types.domain.session import KnowledgeGrant, SessionType


class McpSessionOptions(TypedDict, total=False):
    """The option keywords a granted session adds and a plain one omits.

    Both keys are present together or neither is: a server definition
    without the strict flag would let definitions discovered in the
    session's working directory load alongside it.
    """

    mcp_servers: dict[str, McpServerConfig]
    strict_mcp_config: bool


def map_knowledge_mcp(
    grant: KnowledgeGrant,
    session_type: SessionType,
) -> McpSessionOptions:
    """Session options for *session_type* under *grant*.

    Empty for a session the grant does not name, so the options that
    session constructs are argument-for-argument what it constructed
    before any grant existed.
    """
    if not grant.grants(session_type):
        return McpSessionOptions()

    credential = grant.credential
    if credential is None:
        msg = (
            f"knowledge grant names {session_type.value} but carries no "
            f"credential: {grant.server_name} would be dialled unauthenticated"
        )
        raise ValueError(msg)

    server: McpHttpServerConfig = {
        "type": "http",
        "url": grant.server_url,
        "headers": {grant.auth_header: f"{grant.auth_scheme} {credential}"},
    }
    return McpSessionOptions(
        mcp_servers={grant.server_name: server},
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
    :func:`map_knowledge_mcp` answered for this session: empty means the
    grant does not name it, and its prompt is returned unchanged — byte for
    byte the string the caller passed.  A session told what lives where is
    therefore exactly a session configured to reach it.
    """
    if not attached:
        return prompt
    return f"{grant.knowledge_map}\n\n{prompt}"
