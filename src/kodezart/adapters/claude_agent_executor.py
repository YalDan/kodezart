"""Claude Agent SDK adapter — wraps query(), yields AgentEvent stream."""

from collections.abc import AsyncGenerator, Sequence

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    ProcessError,
    query,
)

from kodezart.adapters._agents_mapping import (
    map_agents,
    map_effort,
    map_model,
    map_system_prompt,
    map_workflow_env,
    map_workflow_settings,
)
from kodezart.adapters._mcp_mapping import (
    map_knowledge_mcp,
    prompt_with_knowledge_map,
)
from kodezart.adapters._permission_modes import _validate_permission_mode
from kodezart.adapters._sdk_mapping import map_message
from kodezart.adapters._skills_mapping import map_setting_sources, map_skills
from kodezart.core.error_egress import redact_credentials
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.session import KnowledgeGrant, SessionType
from kodezart.types.domain.skills import SettingSource, SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)


class ClaudeAgentExecutor:
    """One-shot agent executor using ``query()`` from claude-agent-sdk.

    Each call is an independent conversation.  Implements the AgentExecutor
    protocol.  Not wired by default -- see ``ClaudeClientExecutor`` for the
    production default.
    """

    def __init__(
        self,
        *,
        setting_sources: list[SettingSource],
        knowledge_grant: KnowledgeGrant,
    ) -> None:
        self._setting_sources = setting_sources
        self._knowledge_grant = knowledge_grant
        self._log: BoundLogger = get_logger(__name__)

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection,
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a prompt via one-shot ``query()`` and yield events."""
        await self._log.adebug(
            "executor_stream_start",
            cwd=cwd,
            session_id=session_id,
            permission_mode=permission_mode,
            has_output_format=output_format is not None,
            skills_mode=skills.mode.value,
            session_type=session_type.value,
            agent_count=len(agents),
        )
        knowledge = map_knowledge_mcp(self._knowledge_grant, session_type)
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=_validate_permission_mode(permission_mode),
            allowed_tools=allowed_tools,
            resume=session_id,
            output_format=output_format,
            skills=map_skills(skills),
            setting_sources=map_setting_sources(self._setting_sources),
            agents=map_agents(agents),
            system_prompt=map_system_prompt(session_policy),
            effort=map_effort(session_policy.effort),
            model=map_model(session_policy, None),
            fallback_model=session_policy.fallback_model,
            env=map_workflow_env(session_policy.workflow_access),
            settings=map_workflow_settings(session_policy.workflow_access),
            **knowledge,
        )
        session_prompt = prompt_with_knowledge_map(
            prompt,
            grant=self._knowledge_grant,
            attached=knowledge,
        )
        # TODO: symmetric ProcessError/CLIConnectionError/ClaudeSDKError
        # detail preservation (exit_code, stderr_tail) matching
        # ClaudeClientExecutor when this executor is wired into
        # main.py.lifespan().  Currently unwired in the default
        # composition root (see docs/architecture.md — ClaudeAgentExecutor
        # is the one-shot alternative; ClaudeClientExecutor is the
        # default).  Adding the parallel change here costs CI time on a
        # code path no production deployment exercises.
        try:
            async for message in query(prompt=session_prompt, options=options):
                for event in map_message(message):
                    yield event
        except ProcessError as exc:
            await self._log.awarning(
                "claude_sdk_process_error",
                exit_code=exc.exit_code,
                stderr=(
                    redact_credentials(exc.stderr) if exc.stderr is not None else None
                ),
            )
            raise AgentSDKError(
                str(exc),
                error_kind="ProcessError",
            ) from exc
        except CLIConnectionError as exc:
            await self._log.awarning(
                "claude_sdk_connection_error",
                error=str(exc),
            )
            raise AgentSDKError(
                str(exc),
                error_kind="CLIConnectionError",
            ) from exc
        except ClaudeSDKError as exc:
            await self._log.awarning(
                "claude_sdk_error",
                error=str(exc),
            )
            raise AgentSDKError(
                str(exc),
                error_kind=type(exc).__name__,
            ) from exc
