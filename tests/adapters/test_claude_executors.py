"""Tests for ClaudeAgentExecutor and ClaudeClientExecutor SDK exception wrapping."""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest
import structlog
from claude_agent_sdk import ClaudeAgentOptions, ProcessError

from kodezart.adapters._skills_mapping import map_skills
from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.error_egress import _REDACTION_SENTINEL
from kodezart.domain.errors import AgentSDKError
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import DEFAULT_SETTING_SOURCES, SUPPRESS_ALL_SKILLS


async def _drain(gen: AsyncGenerator[AgentEvent, None]) -> list[AgentEvent]:
    """Consume an async generator into a list."""
    return [event async for event in gen]


def test_agent_executor_instantiates() -> None:
    """ClaudeAgentExecutor can be constructed without side effects."""
    executor = ClaudeAgentExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    assert executor is not None


def test_client_executor_instantiates() -> None:
    """ClaudeClientExecutor can be constructed without side effects."""
    executor = ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    assert executor is not None


def test_agent_sdk_error_preserves_kind() -> None:
    """AgentSDKError stores error_kind for downstream handling."""
    err = AgentSDKError("something broke", error_kind="ProcessError")
    assert err.error_kind == "ProcessError"
    assert "something broke" in str(err)


def test_agent_sdk_error_preserves_process_error_detail() -> None:
    """AgentSDKError stores exit_code and stderr_tail as primitive scalars."""
    err = AgentSDKError(
        "process failed",
        error_kind="ProcessError",
        exit_code=137,
        stderr_tail="oom-killer fired",
    )
    assert err.exit_code == 137
    assert err.stderr_tail == "oom-killer fired"


def test_agent_sdk_error_exit_code_and_stderr_default_none() -> None:
    """exit_code and stderr_tail default to None for non-ProcessError branches."""
    err = AgentSDKError("connection dropped", error_kind="CLIConnectionError")
    assert err.exit_code is None
    assert err.stderr_tail is None


class _FakeSDKClient:
    """Stand-in for ``claude_agent_sdk.ClaudeSDKClient`` that raises on query."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self._exc = exc_to_raise

    async def __aenter__(self) -> "_FakeSDKClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def query(self, prompt: str) -> None:
        _ = prompt
        raise self._exc

    async def receive_response(self) -> AsyncGenerator[Any, None]:  # pragma: no cover
        yield None


async def test_process_error_round_trips_exit_code_and_stderr_on_re_raise() -> None:
    """ProcessError(exit_code, stderr) survives the re-raise on AgentSDKError."""
    boom = ProcessError("boom", exit_code=137, stderr="<known-tail>" * 10)
    executor = ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is not None
    assert "<known-tail>" in err.stderr_tail
    # STDERR_TAIL_BYTES = 4096 — verified at module level.
    assert len(err.stderr_tail) <= 4096


async def test_process_error_with_none_stderr_does_not_crash() -> None:
    """ProcessError(exit_code=137, stderr=None) re-raises with stderr_tail=None."""
    boom = ProcessError("boom", exit_code=137, stderr=None)
    executor = ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    err = excinfo.value
    assert err.error_kind == "ProcessError"
    assert err.exit_code == 137
    assert err.stderr_tail is None


# ---------------------------------------------------------------------------
# Credential redaction — ensures the tokenized URL in ``ProcessError.stderr``
# does not leak through either the structured warning log or the
# ``AgentSDKError.stderr_tail`` field.  Token-named locals are avoided to
# dodge ruff S105 (active in tests).
# ---------------------------------------------------------------------------

_FAKE_GHP_BODY: Final[str] = "A" * 40
_FAKE_URL: Final[str] = (
    f"https://x-access-token:ghp_{_FAKE_GHP_BODY}@github.com/o/r.git"
)


async def test_process_error_redacts_token_in_warning_log() -> None:
    """``claude_sdk_process_error`` log scrubs the credential URL in stderr."""
    stderr_payload = f"git fetch failed: {_FAKE_URL} permission denied"
    boom = ProcessError("git fetch failed", exit_code=128, stderr=stderr_payload)
    executor = ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with structlog.testing.capture_logs() as captured:
            with pytest.raises(AgentSDKError):
                await _drain(
                    executor.stream(
                        skills=SUPPRESS_ALL_SKILLS,
                        prompt="x",
                        cwd="/tmp",
                        permission_mode="default",
                        allowed_tools=[],
                    )
                )
    process_error_records = [
        rec for rec in captured if rec.get("event") == "claude_sdk_process_error"
    ]
    assert process_error_records, "expected a claude_sdk_process_error log record"
    record = process_error_records[0]
    assert "stderr" in record
    stderr_logged = record["stderr"]
    assert stderr_logged is not None
    assert _FAKE_GHP_BODY not in stderr_logged
    assert _REDACTION_SENTINEL in stderr_logged
    # Defense-in-depth: the secret body must not survive in ANY captured
    # record's serialized form.
    for rec in captured:
        assert _FAKE_GHP_BODY not in repr(rec)


async def test_process_error_stderr_tail_on_agent_sdk_error_is_redacted() -> None:
    """``AgentSDKError.stderr_tail`` is redact-before-slice; secret cannot leak."""
    boom = ProcessError("git fetch failed", exit_code=128, stderr=_FAKE_URL)
    executor = ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    with patch(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        lambda **_: _FakeSDKClient(boom),
    ):
        with pytest.raises(AgentSDKError) as excinfo:
            await _drain(
                executor.stream(
                    skills=SUPPRESS_ALL_SKILLS,
                    prompt="x",
                    cwd="/tmp",
                    permission_mode="default",
                    allowed_tools=[],
                )
            )
    assert excinfo.value.stderr_tail is not None
    assert _FAKE_GHP_BODY not in excinfo.value.stderr_tail
    assert _REDACTION_SENTINEL in excinfo.value.stderr_tail


# ---------------------------------------------------------------------------
# KOD-46 — skill selection reaches ClaudeAgentOptions from AppConfig
# ---------------------------------------------------------------------------


def _capture(module: str):
    """Patch the SDK transport in *module* and record the options it receives."""
    recorded: list[ClaudeAgentOptions] = []

    def sink(*args, **kwargs):
        options = kwargs["options"]
        assert isinstance(options, ClaudeAgentOptions)
        recorded.append(options)
        msg = "stop after options"
        raise RuntimeError(msg)

    target = "ClaudeSDKClient" if module.endswith("claude_client_executor") else "query"
    return recorded, patch(f"{module}.{target}", sink)


def _executor_for(module: str):
    """Build the adapter that lives in *module* with configured setting sources."""
    if module.endswith("claude_client_executor"):
        return ClaudeClientExecutor(setting_sources=DEFAULT_SETTING_SOURCES)
    return ClaudeAgentExecutor(setting_sources=DEFAULT_SETTING_SOURCES)


EXECUTOR_MODULES = [
    "kodezart.adapters.claude_client_executor",
    "kodezart.adapters.claude_agent_executor",
]
SKILLS_MATRIX = [
    (SkillsSelection(mode=SkillsMode.NONE), []),
    (SkillsSelection(mode=SkillsMode.ALL), "all"),
    (SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("alpha",)), ["alpha"]),
]


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (SkillsSelection(mode=SkillsMode.NONE), []),
        (SkillsSelection(mode=SkillsMode.ALL), "all"),
        (
            SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("alpha", "beta")),
            ["alpha", "beta"],
        ),
    ],
)
def test_skills_mapping_is_exhaustive_over_the_enum(selection, expected) -> None:
    """NONE -> [], ALL -> "all", EXPLICIT -> the allowlist. Never None."""
    mapped = map_skills(selection)
    assert mapped == expected
    assert mapped is not None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(("selection", "expected"), SKILLS_MATRIX)
async def test_both_executors_pass_the_mapped_skills_never_none(
    module,
    selection,
    expected,
) -> None:
    """Neither adapter has a code path that hands the SDK ``skills=None``."""
    recorded, patcher = _capture(module)
    executor = _executor_for(module)

    with patcher, pytest.raises(RuntimeError, match="stop after options"):
        await _drain(
            executor.stream(
                prompt="p",
                cwd="/tmp/fake",
                permission_mode="plan",
                allowed_tools=[],
                skills=selection,
            )
        )

    assert len(recorded) == 1
    assert recorded[0].skills == expected
    assert recorded[0].skills is not None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(("selection", "expected"), SKILLS_MATRIX)
async def test_setting_sources_come_from_config_in_every_mode(
    module,
    selection,
    expected,
) -> None:
    """AC-1c: the skills knob never silently narrows loaded settings."""
    recorded, patcher = _capture(module)
    executor = _executor_for(module)

    with patcher, pytest.raises(RuntimeError, match="stop after options"):
        await _drain(
            executor.stream(
                prompt="p",
                cwd="/tmp/fake",
                permission_mode="plan",
                allowed_tools=[],
                skills=selection,
            )
        )

    assert recorded[0].setting_sources == ["user", "project", "local"]


def test_no_skill_name_literal_lives_in_the_adapters() -> None:
    """D-2: the configured skill set is data — no hardcoded lists in adapters."""
    adapters = Path(__file__).resolve().parents[2] / "src" / "kodezart" / "adapters"
    for path in adapters.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "skills=[" not in source
        assert 'skills = ["' not in source
