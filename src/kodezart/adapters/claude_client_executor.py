"""Claude interactive executor — uses ClaudeSDKClient."""

from collections.abc import AsyncGenerator, Sequence

# Claude Agent SDK API surface verified against claude-agent-sdk ~=0.2.151
# (ProcessError.exit_code: int | None; ProcessError.stderr: str | None;
# ResultError subclasses ProcessError and adds the CLI's result payload).
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    ProcessError,
    ResultError,
)

from kodezart.adapters._agents_mapping import (
    map_agents,
    map_effort,
    map_model,
    map_settings,
    map_system_prompt,
    map_workflow_env,
)
from kodezart.adapters._mcp_mapping import (
    map_knowledge_mcp,
    prompt_with_knowledge_map,
)
from kodezart.adapters._permission_modes import _validate_permission_mode
from kodezart.adapters._sdk_mapping import INIT_SUBTYPE, map_message
from kodezart.adapters._skills_mapping import map_setting_sources, map_skills
from kodezart.core.constants import STDERR_TAIL_BYTES
from kodezart.core.error_egress import redact_credentials
from kodezart.core.errors import OutputStyleNotConfirmedError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent, SystemEvent
from kodezart.types.domain.session import KnowledgeGrant, SessionType
from kodezart.types.domain.skills import SettingSource, SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)


def _redacted_stderr(exc: ProcessError) -> str | None:
    """The failure's stderr with credentials removed, or nothing at all."""
    if exc.stderr is None:
        return None
    return redact_credentials(exc.stderr)


def _sdk_failure(
    exc: ProcessError,
    stderr_redacted: str | None,
    *,
    error_kind: str,
) -> AgentSDKError:
    """The engine failure for one process-level SDK exception.

    Takes the ALREADY redacted stderr so the warning log and the
    ``stderr_tail`` slice are byte-identical with respect to redaction.
    Redact-before-slice prevents a token straddling the
    ``STDERR_TAIL_BYTES`` boundary from surviving partially exposed.
    """
    return AgentSDKError(
        str(exc),
        error_kind=error_kind,
        exit_code=exc.exit_code,
        stderr_tail=(
            stderr_redacted[:STDERR_TAIL_BYTES] if stderr_redacted is not None else None
        ),
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
        output_style: str | None = None,
    ) -> None:
        self._model = model
        self._setting_sources = setting_sources
        self._knowledge_grant = knowledge_grant
        self._output_style = output_style
        self._log: BoundLogger = get_logger(__name__)

    def _confirm_output_style(self, event: AgentEvent) -> None:
        """Hold the session to the style it was told to run under.

        The opening frame reports the style the CLI really loaded.  A
        declared style it does not confirm means this session's system
        prompt is not the one the operator asked for, so the session
        fails on the frame that said so rather than producing work under
        an unknown style.  Declaring nothing checks nothing: there is no
        claim to confirm.
        """
        if self._output_style is None:
            return
        if not isinstance(event, SystemEvent) or event.subtype != INIT_SUBTYPE:
            return
        if event.output_style == self._output_style:
            return
        raise OutputStyleNotConfirmedError(
            declared=self._output_style,
            reported=event.output_style,
        )

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
            settings=map_settings(
                session_policy.workflow_access,
                self._output_style,
            ),
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
                        self._confirm_output_style(event)
                        yield event
        except ResultError as exc:
            # Ahead of the ProcessError arm, which this SDK type subclasses:
            # below it a terminal error result reaches the workflow as a bare
            # non-zero exit, and the reason the CLI already reported — the
            # result subtype, the terminal reason, the API status — is lost.
            stderr_redacted = _redacted_stderr(exc)
            await self._log.awarning(
                "claude_sdk_result_error",
                exit_code=exc.exit_code,
                subtype=exc.subtype,
                terminal_reason=exc.terminal_reason,
                api_error_status=exc.api_error_status,
                stderr=stderr_redacted,
            )
            raise _sdk_failure(
                exc,
                stderr_redacted,
                error_kind="ResultError",
            ) from exc
        except ProcessError as exc:
            stderr_redacted = _redacted_stderr(exc)
            await self._log.awarning(
                "claude_sdk_process_error",
                exit_code=exc.exit_code,
                stderr=stderr_redacted,
            )
            raise _sdk_failure(
                exc,
                stderr_redacted,
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
