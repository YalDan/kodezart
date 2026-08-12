"""One pass, sent as one session: the render path and the send that follows.

The grooming render is asserted against the shipped template and the
shipped example operation config — the same artifacts the fire-prep render
is asserted against — because a render path tested against a fixture body
proves nothing about the prompt the deployment would actually send.
"""

from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import PromptRenderError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.services.grooming_pass import compose_grooming_prompt
from kodezart.services.pass_session import PassSession
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.session import SessionType
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"

WORKSPACE = "/tmp/kodezart-scheduled-pass"
PERMISSION_MODE = "bypassPermissions"


def example_config() -> OperationConfig:
    return load_operation_config(EXAMPLE)


def session_for(compose) -> tuple[PassSession, FakeAgentRunner]:
    """A pass session over a runner that records what it was sent."""
    runner = FakeAgentRunner(events=[])
    return (
        PassSession(
            name="grooming",
            compose=compose,
            runner=runner,
            workspace_path=WORKSPACE,
            permission_mode=PERMISSION_MODE,
            allowed_tools=["Bash"],
            skills=SUPPRESS_ALL_SKILLS,
            session_type=SessionType.SCHEDULED_PASS,
        ),
        runner,
    )


def test_the_grooming_prompt_composes_through_the_registry() -> None:
    """The mirror of the fire-prep render: template plus operation config."""
    config = example_config()
    rendered = compose_grooming_prompt(
        load_registry(bindings=dict(bindings_for(config))),
    )

    assert rendered
    assert "{{" not in rendered
    assert config.operation_name in rendered


def test_an_unbound_placeholder_is_a_typed_refusal_not_a_prompt() -> None:
    """No config value, no grooming prompt, and the placeholder is named."""
    with pytest.raises(PromptRenderError) as excinfo:
        compose_grooming_prompt(load_registry())

    assert "operation_name" in excinfo.value.missing


async def test_the_session_receives_the_rendered_prompt_and_its_grant() -> None:
    """What reaches the query path is what the registry rendered."""
    registry = load_registry(bindings=dict(bindings_for(example_config())))
    rendered = compose_grooming_prompt(registry)
    session, runner = session_for(lambda: compose_grooming_prompt(registry))

    await session.run()

    assert runner.calls == [
        {
            "method": "stream_in_workspace",
            "prompt": rendered,
            "workspace_path": WORKSPACE,
            "session_id": None,
            "session_type": SessionType.SCHEDULED_PASS,
        },
    ]


async def test_a_prompt_that_cannot_render_starts_no_session() -> None:
    """Fail loudly rather than send a hole: the failure precedes the send."""
    session, runner = session_for(lambda: compose_grooming_prompt(load_registry()))

    with pytest.raises(PromptRenderError):
        await session.run()

    assert runner.calls == []
