"""Fresh structured judgment in an already owned workspace."""

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import soft_failure
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.core.stream_drain import drain
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


async def judge_in_workspace(
    *,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    workspace: str,
    key: PromptKey,
    prompt: str,
    output_schema: dict[str, object],
    site: RaiseSite,
    session_type: SessionType,
    failure_message: str,
) -> dict[str, object]:
    """Run a fresh read-only judgment inside the caller's owned workspace.

    The caller retains workspace/source validation and output interpretation.
    Session type and error context belong to that caller's phase.
    """
    result, rate_limited = await drain(
        runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=workspace,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=list(EVAL_TOOLS),
            skills=prompts.session_skills(key, skills),
            session_type=session_type,
            agents=NO_SUBAGENTS,
            session_policy=prompts.session_policy(key),
            session_id=None,
            output_format={"type": "json_schema", "schema": output_schema},
        ),
        site=site,
    )
    if (
        result is None
        or result.structured_output is None
        or result.is_error
        or rate_limited
    ):
        raise soft_failure(
            failure_message,
            raise_site=site,
            result_event=result,
            rate_limit_rejected=rate_limited,
        )
    return result.structured_output
