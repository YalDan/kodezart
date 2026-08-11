"""Claude interactive executor — uses ClaudeSDKClient."""

from collections.abc import AsyncGenerator, Sequence

# Claude Agent SDK API surface verified against claude-agent-sdk ~=0.2.128
# (ProcessError.exit_code: int | None; ProcessError.stderr: str | None).
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    ProcessError,
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
from kodezart.core.constants import STDERR_TAIL_BYTES
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


class ClaudeClientExecutor:
    """Agent executor using the persistent SDK client.

    Same AgentExecutor protocol as ClaudeAgentExecutor, different transport.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        setting_sources: list[SettingSource],
        knowledge_grant: KnowledgeGrant,
    ) -> None:
        self._model = model
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
        """Open a persistent Claude SDK session and yield events.

        Supports session resume via *session_id* and structured JSON
        output via *output_format*.  A per-call model on *session_policy*
        overrides the model this executor was constructed with.
        """
        await self._log.adebug(
            "client_executor_stream_start",
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
            permission_mode=_validate_permission_mode(
                permission_mode,
            ),
            allowed_tools=allowed_tools,
            resume=session_id,
            output_format=output_format,
            model=map_model(session_policy, self._model),
            skills=map_skills(skills),
            setting_sources=map_setting_sources(self._setting_sources),
            agents=map_agents(agents),
            system_prompt=map_system_prompt(session_policy),
            effort=map_effort(session_policy.effort),
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
        try:
            async with ClaudeSDKClient(
                options=options,
            ) as client:
                await client.query(session_prompt)
                async for message in client.receive_response():
                    for event in map_message(message):
                        yield event
        except ProcessError as exc:
            # Redact ONCE into a local so the awarning kwarg and the
            # AgentSDKError.stderr_tail slice are byte-identical with
            # respect to redaction.  Redact-before-slice prevents a
            # token straddling the STDERR_TAIL_BYTES boundary from
            # surviving partially exposed in stderr_tail.
            stderr_redacted: str | None = (
                redact_credentials(exc.stderr) if exc.stderr is not None else None
            )
            await self._log.awarning(
                "claude_sdk_process_error",
                exit_code=exc.exit_code,
                stderr=stderr_redacted,
            )
            stderr_tail: str | None = (
                stderr_redacted[:STDERR_TAIL_BYTES]
                if stderr_redacted is not None
                else None
            )
            raise AgentSDKError(
                str(exc),
                error_kind="ProcessError",
                exit_code=exc.exit_code,
                stderr_tail=stderr_tail,
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
