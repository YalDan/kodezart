"""Resolution of the knowledge grant — one decision, both its consequences.

Attaching the knowledge server gives a session the capability; the
what-lives-where map tells it what lives where.  Both are decided by the
same grant list, so both are resolved here, into one value.
"""

from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import PromptProvider
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import KnowledgeGrant


async def boot_knowledge_grant(
    *,
    config: AppConfig,
    prompts: PromptProvider,
    log: BoundLogger,
) -> KnowledgeGrant:
    """The resolved grant, carrying the map a granted session is preluded with.

    The map is rendered HERE, at boot, from the operation-config bindings
    the registry holds.  That is what makes an unresolvable destination a
    startup failure naming the reference, rather than a hole discovered in
    a live prompt: the renderer collects every unbound reference into one
    error, and this call is the boot act that raises it.

    A configuration that grants nothing renders nothing.  There is no
    session to prelude, and rendering would demand knowledge references
    from an operation that legitimately declares none — the shipped grant
    list is empty, so that is the ordinary case, not the exception.
    """
    if not config.knowledge_session_grants:
        return config.knowledge_grant(knowledge_map="")

    knowledge_map = prompts.template_for(PromptKey.KNOWLEDGE_MAP).render({})
    await log.ainfo(
        "knowledge_map_rendered",
        granted=[
            session_type.value for session_type in config.knowledge_session_grants
        ],
        characters=len(knowledge_map),
    )
    return config.knowledge_grant(knowledge_map=knowledge_map)
