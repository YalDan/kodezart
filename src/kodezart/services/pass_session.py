"""One judgment-pass session: render a prompt key, get a typed answer back.

The seam between the two halves KOD-60 R8 splits.  Everything model-shaped
is here — the session, the timeout, the failure taxonomy — and everything
that reaches a surface outside the process is on the other side of it, in
the pass services that own the gate and the port.

The session is given no way to write.  Its answer is structured output,
validated into a frozen model at this boundary; a malformed answer is a
typed failure and never a partially-applied pass.  What tools it holds is
the caller's decision, because the two passes need different ones: a
preparation session reads a work set that was rendered into its prompt and
needs nothing, and a verification session has to run a repository's own
commands or it is reporting a build it did not perform.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from kodezart.core.errors import PromptRenderError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import AgentExecutor, PromptProvider
from kodezart.core.stream_drain import drain
from kodezart.types.domain.passes import PassSessionFailure
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection

_SESSION_PERMISSION_MODE = "default"

_Output = TypeVar("_Output", bound=BaseModel)


class PassSession:
    """Dispatches one pass prompt and validates the answer it comes back with."""

    def __init__(
        self,
        *,
        executor: AgentExecutor,
        prompts: PromptProvider,
        skills: SkillsSelection,
        timeout_seconds: float,
    ) -> None:
        self._executor: AgentExecutor = executor
        self._prompts: PromptProvider = prompts
        self._skills: SkillsSelection = skills
        self._timeout_seconds: float = timeout_seconds
        self._log: BoundLogger = get_logger(__name__)

    async def compose(
        self,
        *,
        key: PromptKey,
        variables: Mapping[str, object],
        schema: Mapping[str, object],
        model: type[_Output],
        cwd: str,
        allowed_tools: Sequence[str],
    ) -> _Output | PassSessionFailure:
        """Run *key* as one session; return its answer or the reason there is none.

        Never raises for a session that did not answer.  A pass driven by
        the scheduler has no caller to hand an exception to, and an
        exception escaping a tick is not a report.
        """
        try:
            prompt = self._prompts.template_for(key).render(variables)
        except PromptRenderError as exc:
            await self._log.aerror(
                "pass_session_prompt_unrenderable",
                pass_key=key.value,
                missing=list(exc.missing),
            )
            return PassSessionFailure.NOT_RENDERABLE

        returned = await self._dispatch(
            prompt=prompt,
            schema=schema,
            cwd=cwd,
            allowed_tools=allowed_tools,
        )
        if isinstance(returned, PassSessionFailure):
            await self._log.awarning(
                "pass_session_unanswered",
                pass_key=key.value,
                failure=returned.value,
            )
            return returned
        try:
            answer = model.model_validate(returned)
        except ValidationError:
            await self._log.awarning(
                "pass_session_unanswered",
                pass_key=key.value,
                failure=PassSessionFailure.MALFORMED_OUTPUT.value,
            )
            return PassSessionFailure.MALFORMED_OUTPUT
        await self._log.ainfo("pass_session_answered", pass_key=key.value)
        return answer

    async def _dispatch(
        self,
        *,
        prompt: str,
        schema: Mapping[str, object],
        cwd: str,
        allowed_tools: Sequence[str],
    ) -> dict[str, object] | PassSessionFailure:
        """One session, mapped onto its raw structured output or a failure kind."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result_event, _ = await drain(
                    self._executor.stream(
                        prompt=prompt,
                        cwd=cwd,
                        permission_mode=_SESSION_PERMISSION_MODE,
                        allowed_tools=list(allowed_tools),
                        skills=self._skills,
                        output_format={"type": "json_schema", "schema": dict(schema)},
                    ),
                )
        except TimeoutError:
            return PassSessionFailure.TIMEOUT
        except OSError:
            return PassSessionFailure.TRANSPORT_ERROR

        if result_event is None or result_event.structured_output is None:
            return PassSessionFailure.EMPTY_RESPONSE
        if result_event.is_error:
            return PassSessionFailure.ERRORED
        return result_event.structured_output
