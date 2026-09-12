"""Agent settings reach native preflight, prompt policies and SDK sessions."""

import json

import pytest
from claude_agent_sdk import SystemMessage
from pydantic import ValidationError

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.composition.preflight import boot_skills
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import OutputStyleNotConfirmedError, SkillPreflightError
from kodezart.core.logging import get_logger
from kodezart.types.domain.agent import SystemEvent
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import PermissionMode, SessionType
from tests.core.test_retired_config import _from_source
from tests.fakes import NO_KNOWLEDGE_GRANT, _recording_client, make_prompt_provider

OLD = [
    "model",
    "fallback_model",
    "session_models",
    "claude_output_style",
    "claude_home_dir",
    "setting_sources",
    "skills_mode",
    "skills_allowlist",
]


def provisioned_home(tmp_path):
    home = tmp_path / "host"
    registry = make_prompt_provider()
    names = sorted(
        {"fixture"}
        | {name for key in PromptKey for name in registry.declared_skills(key)}
    )
    plugins = {}
    for name in names:
        if ":" in name:
            plugin, skill = name.split(":", 1)
            install = home / "installed" / plugin
            plugins[plugin + "@fixture"] = [
                {"scope": "user", "installPath": str(install)}
            ]
            path = install / "skills" / skill
        else:
            path = home / "skills" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text("Fixture skill")
    (home / "plugins").mkdir(exist_ok=True)
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": plugins})
    )
    return home, names


def configured(source, tmp_path, monkeypatch):
    home, names = provisioned_home(tmp_path)
    values = {
        "model": "primary-engine",
        "fallback_model": "fallback-engine",
        "session_models": {"implementation": "role-engine"},
        "output_style": "Concise",
        "home_dir": str(home),
        "setting_sources": ["user"],
        "skills": {"mode": "explicit", "allowlist": names},
    }
    if source == "init":
        return AppConfig(_env_file=None, agent=values)
    if source == "env":
        for key, value in values.items():
            monkeypatch.setenv(
                "KODEZART_AGENT__" + key.upper(),
                value if isinstance(value, str) else json.dumps(value),
            )
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(
            "\n".join(
                "KODEZART_AGENT__"
                + key.upper()
                + "='"
                + (value if isinstance(value, str) else json.dumps(value))
                + "'"
                for key, value in values.items()
            )
        )
        return AppConfig(_env_file=path)
    (tmp_path / "KODEZART_AGENT").write_text(json.dumps(values))
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
async def test_agent_sources_reach_native_inventory_policies_and_session(
    source, tmp_path, monkeypatch
):
    config = configured(source, tmp_path, monkeypatch)
    log = get_logger(__name__)
    prompts = await boot_prompts(config=config, operation=None, log=log)
    skills = await boot_skills(settings=config.agent, prompts=prompts, log=log)
    assert skills is config.agent.skills
    for key in PromptKey:
        policy = prompts.session_policy(key)
        assert policy.fallback_model == "fallback-engine"
        assert policy.model == (
            "role-engine" if key is PromptKey.IMPLEMENTATION else None
        )
    recorded = []
    monkeypatch.setattr(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        _recording_client(
            recorded,
            [
                SystemMessage(
                    subtype="init",
                    data={
                        "model": "independent-server-model",
                        "output_style": "Concise",
                    },
                )
            ],
        ),
    )
    executor = ClaudeClientExecutor(
        model=config.agent.model,
        setting_sources=config.agent.setting_sources,
        output_style=config.agent.output_style,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    for key, expected in [
        (PromptKey.IMPLEMENTATION, "role-engine"),
        (PromptKey.EVALUATION, "primary-engine"),
    ]:
        events = [
            event
            async for event in executor.stream(
                prompt="probe",
                cwd=str(tmp_path),
                permission_mode=PermissionMode.INTERACTIVE,
                allowed_tools=[],
                skills=skills,
                session_type=SessionType.API_QUERY,
                session_policy=prompts.session_policy(key),
            )
        ]
        options = recorded[-1].options
        assert options.model == expected
        assert options.fallback_model == "fallback-engine"
        assert options.setting_sources == ["user"]
        assert options.skills == list(skills.allowlist)
        assert json.loads(options.settings)["outputStyle"] == "Concise"
        (opening,) = [event for event in events if isinstance(event, SystemEvent)]
        assert opening.data["model"] == "independent-server-model"
        assert opening.output_style == "Concise"


async def test_missing_host_skills_refuse_before_any_session(tmp_path):
    config = AppConfig(
        _env_file=None,
        agent={
            "home_dir": str(tmp_path),
            "skills": {"mode": "explicit", "allowlist": ["absent-one", "absent-two"]},
        },
    )
    with pytest.raises(SkillPreflightError) as caught:
        await boot_skills(
            settings=config.agent,
            prompts=make_prompt_provider(),
            log=get_logger(__name__),
        )
    assert caught.value.unresolvable == ("absent-one", "absent-two")


async def test_declared_style_still_requires_native_confirmation(tmp_path, monkeypatch):
    config = configured("init", tmp_path, monkeypatch)
    recorded = []
    monkeypatch.setattr(
        "kodezart.adapters.claude_client_executor.ClaudeSDKClient",
        _recording_client(
            recorded,
            [
                SystemMessage(
                    subtype="init", data={"model": "server", "output_style": "Other"}
                )
            ],
        ),
    )
    executor = ClaudeClientExecutor(
        model=config.agent.model,
        setting_sources=config.agent.setting_sources,
        output_style=config.agent.output_style,
        knowledge_grant=NO_KNOWLEDGE_GRANT,
    )
    with pytest.raises(OutputStyleNotConfirmedError):
        _ = [
            event
            async for event in executor.stream(
                prompt="probe",
                cwd=str(tmp_path),
                permission_mode=PermissionMode.INTERACTIVE,
                allowed_tools=[],
                skills=config.agent.skills,
                session_type=SessionType.API_QUERY,
            )
        ]


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize("field", OLD)
def test_retired_agent_names_refuse(source, field, tmp_path, monkeypatch):
    values = {
        "model": "fixture",
        "fallback_model": "fixture",
        "session_models": {},
        "claude_output_style": "fixture",
        "claude_home_dir": "/fixture",
        "setting_sources": ["user"],
        "skills_mode": "none",
        "skills_allowlist": [],
    }
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, values[field], tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"mode": "explicit"},
        {"mode": "none", "allowlist": ["a"]},
        {"mode": "all", "allowlist": ["a"]},
        {"mode": "unsupported"},
        {"mode": "none", "allowlits": []},
    ],
)
def test_existing_selection_invariant_and_shape_refuse(value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, agent={"skills": value})


def test_unknown_prompt_key_remains_named_without_its_value():
    with pytest.raises(ValidationError) as caught:
        AppConfig(
            _env_file=None,
            agent={"session_models": {"implemenation": "synthetic-model-value"}},
        )
    message = str(caught.value)
    assert "implemenation" in message and "implementation" in message
    assert "synthetic-model-value" not in message


def test_defaults_and_source_precedence(tmp_path, monkeypatch):
    config = AppConfig(_env_file=None)
    assert config.agent.model is None and config.agent.fallback_model is None
    assert config.agent.session_models == {} and config.agent.output_style is None
    assert config.agent.home_dir == "~/.claude"
    assert config.agent.setting_sources == ["user", "project", "local"]
    assert (
        config.agent.skills.mode.value == "none" and config.agent.skills.allowlist == ()
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_AGENT").write_text(json.dumps({"model": "secret"}))
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_AGENT__MODEL=dotenv\n")
    monkeypatch.setenv("KODEZART_AGENT__MODEL", "env")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert AppConfig(**kwargs, agent={"model": "init"}).agent.model == "init"
    assert AppConfig(**kwargs).agent.model == "env"
    monkeypatch.delenv("KODEZART_AGENT__MODEL")
    assert AppConfig(**kwargs).agent.model == "dotenv"
    assert AppConfig(_env_file=None, _secrets_dir=secrets).agent.model == "secret"


async def test_provisioned_allowlist_missing_role_skills_refuses_at_boot(tmp_path):
    home, _ = provisioned_home(tmp_path)
    config = AppConfig(
        _env_file=None,
        agent={
            "home_dir": str(home),
            "skills": {"mode": "explicit", "allowlist": ["fixture"]},
        },
    )
    with pytest.raises(SkillPreflightError, match="Prompt-set"):
        await boot_skills(
            settings=config.agent,
            prompts=make_prompt_provider(),
            log=get_logger(__name__),
        )


def test_unknown_agent_field_refuses_without_echoing_value():
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        AppConfig(_env_file=None, agent={"modle": "private-model-value"})
    assert "modle" in str(caught.value)
    assert "private-model-value" not in str(caught.value)
