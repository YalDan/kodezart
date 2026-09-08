"""The active graph's application bundles become the original native tool lists."""

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    ResultEvent,
    SystemEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.session import ToolPreset
from tests.chains.test_workflow_phase_composition import (
    ObservedExecutor,
    assert_released,
    drive,
    setup,
)
from tests.fakes import NO_KNOWLEDGE_GRANT


@pytest.mark.parametrize("standalone", [False, True])
async def test_actual_graph_presets_reach_native_sessions(
    tmp_path, monkeypatch, standalone
):
    observer = ObservedExecutor()
    options_seen, closed = [], []

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
            options = self.options
            async for event in observer.stream(
                prompt=self.prompt,
                cwd=options.cwd,
                permission_mode=options.permission_mode,
                allowed_tools=options.allowed_tools,
                output_format=options.output_format,
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
                    raise AssertionError(
                        f"Unmapped scripted boundary event {type(event)}"
                    )

    monkeypatch.setattr(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient", Client
    )
    executor = ClaudeClientExecutor(
        setting_sources=[], knowledge_grant=NO_KNOWLEDGE_GRANT
    )
    router, workspace, repo, _, _, request = await setup(tmp_path, executor)
    request["allowed_tools"] = ToolPreset.IMPLEMENTATION
    events = [event async for event in drive(router, standalone, request)]
    (terminal,) = [
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    ]
    assert terminal.accepted and terminal.merged and terminal.total_iterations == 1
    assert len(options_seen) == len(observer.calls) == len(closed)
    seen = set()
    for call, options in zip(observer.calls, options_seen, strict=True):
        schema = (
            {}
            if options.output_format is None
            else options.output_format["schema"]["properties"]
        )
        if call["role"] == "implementation":
            expected = ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]
            seen.add("implementation")
        elif "criteria" in schema:
            expected = ["Read", "Glob", "Grep", "Bash", "Agent"]
            seen.add("delegated_evaluation")
        elif call["role"] in {"validation", "evaluation", "review"} or "body" in schema:
            expected = ["Read", "Glob", "Grep", "Bash"]
            seen.add("evaluation")
        elif "requiredChanges" in schema or "approved" in schema:
            expected = [
                "Read",
                "Glob",
                "Grep",
                "Bash",
                "Agent",
                "WebSearch",
                "WebFetch",
            ]
            seen.add("authoring")
        else:
            expected = []
        assert options.allowed_tools == expected
    assert seen == {"implementation", "delegated_evaluation", "evaluation", "authoring"}
    await assert_released(workspace, repo)
