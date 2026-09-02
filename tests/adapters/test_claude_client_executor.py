"""ClaudeClientExecutor: the failure vocabulary the 0.2.151 bump widened,
and the output style the session is held to.

``ResultError`` (SDK 0.2.140) subclasses ``ProcessError``, so it arrives at
an executor that never heard of it as a bare non-zero exit — the reason the
CLI already reported goes to the log's exit code and nowhere else.  Both
arms are pinned here together, because the fix is their ORDER: the pair is
the test, not either half of it.

KOD-292 adds the other direction.  An output style is a system-prompt
modification the SDK exposes only through the settings object, so what a
session ASKED for and what it RUNS under are two different facts: the
declaration goes out on ``settings``, the opening frame reports back what
was loaded, and the session fails when they disagree.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast
from unittest.mock import patch

import pytest
from claude_agent_sdk import ProcessError, ResultError, SystemMessage

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.errors import OutputStyleNotConfirmedError
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent, SystemEvent
from tests.fakes import (
    DEFAULT_SETTING_SOURCES,
    FAKE_SESSION_TYPE,
    NO_KNOWLEDGE_GRANT,
    SUPPRESS_ALL_SKILLS,
    recorded_session,
)

_EXECUTOR_MODULE = "kodezart.adapters.claude_client_executor"

#: The style the operation declares, and one the operator did not.
_DECLARED_STYLE = "Concise"
_OTHER_STYLE = "Explanatory"


class _RaisingSDKClient:
    """Stand-in for the persistent SDK client that fails the query."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self._exc = exc_to_raise

    async def __aenter__(self) -> "_RaisingSDKClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        _ = prompt
        raise self._exc

    async def receive_response(self) -> AsyncGenerator[Any, None]:
        yield None


async def _failure_of(exc: Exception) -> AgentSDKError:
    """The engine failure one session raises when the SDK raises *exc*."""
    executor = ClaudeClientExecutor(
        setting_sources=DEFAULT_SETTING_SOURCES,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    events: list[AgentEvent] = []
    with (
        patch(
            f"{_EXECUTOR_MODULE}.ClaudeSDKClient",
            lambda **_: _RaisingSDKClient(exc),
        ),
        pytest.raises(AgentSDKError) as excinfo,
    ):
        async for event in executor.stream(
            prompt="x",
            cwd="/tmp",
            permission_mode="default",
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
        ):
            events.append(event)
    assert events == []
    return excinfo.value


async def test_a_terminal_error_result_is_classified_as_its_own_kind() -> None:
    """The measured gap: ``ResultError`` read as a plain ``ProcessError``.

    It reaches the workflow through the existing engine-failure class, so
    nothing downstream learns a new exception — but it names its own kind,
    which is the fact a bare exit code could not carry.
    """
    boom = ResultError(
        "terminal error result",
        {
            "subtype": "error_during_execution",
            "terminal_reason": "api_error",
            "api_error_status": 529,
            "session_id": "session-1",
        },
        exit_code=1,
    )

    failure = await _failure_of(boom)

    assert failure.error_kind == "ResultError"
    assert failure.exit_code == 1
    assert failure.stderr_tail is None


async def test_a_plain_process_error_keeps_the_kind_it_always_had() -> None:
    """The paired negative: the new arm narrows nothing it should not."""
    boom = ProcessError("boom", exit_code=137, stderr="<known-tail>")

    failure = await _failure_of(boom)

    assert failure.error_kind == "ProcessError"
    assert failure.exit_code == 137
    assert failure.stderr_tail is not None
    assert "<known-tail>" in failure.stderr_tail


# ---------------------------------------------------------------------------
# KOD-292 — the declared output style, and the frame that confirms it
# ---------------------------------------------------------------------------


def _init(style: str | None) -> SystemMessage:
    """The opening frame, reporting the engine and the style it loaded.

    ``style`` of ``None`` is the frame a CLI too old for the option sends:
    it names the model and says nothing at all about a style, which is the
    silence that has to fail rather than pass.
    """
    data: dict[str, object] = {"model": "engine-1"}
    if style is not None:
        data["output_style"] = style
    return SystemMessage(subtype="init", data=data)


def _init_event(session_events: tuple[AgentEvent, ...]) -> SystemEvent:
    """The one opening frame the session put on the stream."""
    opening = [
        event
        for event in session_events
        if isinstance(event, SystemEvent) and event.subtype == "init"
    ]
    assert len(opening) == 1
    return opening[0]


async def test_a_declared_style_travels_as_the_settings_object() -> None:
    """The declaration's only route: the SDK exposes no style option."""
    session = await recorded_session(
        _EXECUTOR_MODULE,
        output_style=_DECLARED_STYLE,
        messages=[_init(_DECLARED_STYLE)],
    )

    settings = cast(Any, session.options).settings

    assert json.loads(settings) == {"outputStyle": _DECLARED_STYLE}


async def test_declaring_no_style_sends_no_settings_at_all() -> None:
    """The paired negative: absence maps to absence, not to a chosen style.

    Sending an empty settings object would outrank the operator's own
    settings sources with nothing — which is a decision, made in code,
    about a session this deployment declined to decide about.
    """
    session = await recorded_session(
        _EXECUTOR_MODULE,
        messages=[_init(_OTHER_STYLE)],
    )

    assert cast(Any, session.options).settings is None


async def test_a_confirmed_style_rides_the_frame_that_reports_the_engine() -> None:
    """What the session RUNS under, on the event that already names its engine."""
    session = await recorded_session(
        _EXECUTOR_MODULE,
        output_style=_DECLARED_STYLE,
        messages=[_init(_DECLARED_STYLE)],
    )

    opening = _init_event(session.events)

    assert opening.output_style == _DECLARED_STYLE
    assert opening.data["model"] == "engine-1"


async def test_an_undeclared_style_still_reports_what_the_frame_says() -> None:
    """Nothing is checked, and nothing is hidden: the frame is still read.

    A deployment that declares no style has no claim to confirm, but the
    style it happened to run under is exactly the fact that made this
    knob necessary, so it reaches the stream either way.
    """
    session = await recorded_session(
        _EXECUTOR_MODULE,
        messages=[_init(_OTHER_STYLE)],
    )

    assert _init_event(session.events).output_style == _OTHER_STYLE


@pytest.mark.parametrize(
    ("reported", "case"),
    [(_OTHER_STYLE, "another style"), (None, "no style at all")],
)
async def test_a_declared_style_the_frame_does_not_confirm_fails_the_session(
    reported: str | None,
    case: str,
) -> None:
    """The measured failure this exists for: a style assumed, never verified.

    Both unconfirmed shapes fail identically — a frame naming a different
    style, and a frame from a CLI that does not know the option — because
    the harm is the same in both: work produced under a system prompt the
    operator did not ask for.
    """
    with pytest.raises(OutputStyleNotConfirmedError) as excinfo:
        await recorded_session(
            _EXECUTOR_MODULE,
            output_style=_DECLARED_STYLE,
            messages=[_init(reported)],
        )

    assert excinfo.value.declared == _DECLARED_STYLE
    assert excinfo.value.reported == reported
    assert _DECLARED_STYLE in str(excinfo.value), case
