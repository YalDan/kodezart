"""Mapping the resolved knowledge grant onto SDK session options.

One helper serves both executor option-construction sites, so the grant
decision is taken in exactly one expression.  That is what keeps the
unwired executor from becoming a hole: it is not covered by a second copy
of the rule, it is covered by the same one.
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
