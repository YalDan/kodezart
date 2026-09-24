"""One question put to an agent, answered in a typed shape.

A prompt key names the question.  Its template, rendered with the caller's
per-call bindings, goes to one short unattended session of the scheduled
pass kind, which reaches the tracker through the server that kind is given:
no allowlist, no subagents.  The answer is demanded in the key's wire schema
and read into the caller's output model; the caller keeps only the
arithmetic around it.

A stream that ends with no structured output, or with one the model
refuses, is named (``agent_question_unanswered``) and answered ``None``:
what that means is the caller's to decide.  A raise is not an answer and
propagates.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from kodezart.core.constants import UNATTENDED_PERMISSION_MODE
from kodezart.core.error_egress import redact_credentials
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentRunner, PromptSetProvider
from kodezart.core.stream_drain import drain
from kodezart.types.domain.agent import PASS_GATE_SCHEMA, RaiseSite
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS

_log: BoundLogger = get_logger(__name__)

#: The site each question's drained stream is named by.
_SITES: Mapping[PromptKey, RaiseSite] = {PromptKey.PASS_GATE: "pass_gate"}
#: The wire schema each question's answer is demanded in, by the precomputed
#: constant every dispatch site names.
_OUTPUT_FORMATS: Mapping[PromptKey, dict[str, object]] = {
    PromptKey.PASS_GATE: {"type": "json_schema", "schema": PASS_GATE_SCHEMA},
}


async def ask[T: BaseModel](
    *,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    workspace_path: str,
    key: PromptKey,
    bindings: Mapping[str, object],
    answer: type[T],
) -> T | None:
    """Ask *key*'s question over *bindings*: the validated *answer*, or ``None``."""
    policy = prompts.session_policy(key)
    prompt = prompts.template_for(key).render(bindings)
    await _log.ainfo(
        "agent_question_asked",
        key=key.value,
        model=policy.model,
        effort=None if policy.effort is None else policy.effort.value,
    )
    result, _rate_limit_rejected = await drain(
        runner.stream_in_workspace(
            prompt=prompt,
            workspace_path=workspace_path,
            permission_mode=UNATTENDED_PERMISSION_MODE,
            allowed_tools=[],
            skills=prompts.session_skills(key, skills),
            session_type=SessionType.SCHEDULED_PASS,
            agents=NO_SUBAGENTS,
            session_policy=policy,
            output_format=_OUTPUT_FORMATS[key],
        ),
        site=_SITES[key],
    )
    if result is None or result.structured_output is None:
        await _log.awarning(
            "agent_question_unanswered",
            key=key.value,
            error="the session ended with no structured answer",
        )
        return None
    try:
        parsed = answer.model_validate(result.structured_output)
    except ValidationError as exc:
        await _log.awarning(
            "agent_question_unanswered",
            key=key.value,
            error=redact_credentials(str(exc)),
        )
        return None
    await _log.ainfo("agent_question_answered", key=key.value)
    return parsed
