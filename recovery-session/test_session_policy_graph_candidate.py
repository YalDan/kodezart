"""Existing real Git workflow carries the extracted policy through both SDKs."""

from dataclasses import replace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent, SystemEvent
from tests.fakes import EXECUTOR_MODULES, SUPPRESS_ALL_SKILLS, executor_for
from tests.integration import test_workflow_e2e as actual
from tests.integration.test_workflow_e2e import git_env as git_env


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_existing_real_workflow_tool_policy_reaches_native_options(
    git_env, tmp_path, monkeypatch, module
):
    factory = actual.ScriptedFakeExecutor
    scripts, options_seen, closed = [], [], []

    def executor(**kwargs):
        scripts.append(factory(**kwargs))
        return executor_for(module)

    async def messages(prompt, options):
        async for event in scripts[0].stream(
            prompt=prompt,
            cwd=options.cwd,
            permission_mode=options.permission_mode,
            allowed_tools=options.allowed_tools,
            output_format=options.output_format,
            skills=SUPPRESS_ALL_SKILLS,
        ):
            if isinstance(event, SystemEvent):
                yield SystemMessage(subtype=event.subtype, data=event.data)
            elif isinstance(event, AssistantTextEvent):
                yield AssistantMessage(
                    content=[TextBlock(text=event.text)], model=event.model
                )
            elif isinstance(event, ResultEvent):
                yield ResultMessage(
                    subtype=event.subtype,
                    duration_ms=event.duration_ms,
                    duration_api_ms=event.duration_api_ms,
                    is_error=event.is_error,
                    num_turns=event.num_turns,
                    session_id=event.session_id,
                    structured_output=event.structured_output,
                )
            else:
                raise AssertionError(type(event))

    class Client:
        def __init__(self, *, options):
            self.options = options
            options_seen.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self.options)

        async def query(self, prompt, session_id=None):
            self.prompt = prompt

        async def receive_response(self):
            async for message in messages(self.prompt, self.options):
                yield message

    async def query(*, prompt, options):
        options_seen.append(options)
        try:
            async for message in messages(prompt, options):
                yield message
        finally:
            closed.append(options)

    monkeypatch.setattr(actual, "ScriptedFakeExecutor", executor)
    persistent = module.endswith("claude_client_executor")
    monkeypatch.setattr(
        f"{module}.{'ClaudeSDKClient' if persistent else 'query'}",
        Client if persistent else query,
    )
    await actual.test_workflow_e2e_creates_branch_and_pushes(git_env, tmp_path)
    assert len(scripts) == 1
    assert len(options_seen) == len(scripts[0].calls) == len(closed)
    assert {tuple(o.allowed_tools) for o in options_seen} == {
        (),
        ("Bash",),
        ("Read", "Glob", "Grep", "Bash"),
        ("Read", "Glob", "Grep", "Bash", "Agent"),
        ("Read", "Glob", "Grep", "Bash", "Agent", "WebSearch", "WebFetch"),
    }
    for options in options_seen:
        assert options.tools is None
        command = SubprocessCLITransport(
            "Inspect", replace(options, cli_path="/fixture/claude")
        )._build_command()
        assert "--tools" not in command
        assert command[command.index("--permission-mode") + 1] == options.permission_mode
        assert options.permission_mode in {"plan", "bypassPermissions"}
