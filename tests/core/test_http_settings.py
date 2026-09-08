"""HTTP settings reach FastAPI and its real response-link consumers."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from kodezart.api import dependencies as deps
from kodezart.core.config import AppConfig
from kodezart.main import create_app
from kodezart.types.domain.agent import AssistantTextEvent
from tests.api.v1.test_dependencies import BODY, events
from tests.core.test_retired_config import _from_source
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner, FakeJobQueue

FIELDS = {"project_name": "Fixture HTTP", "debug": True, "api_v1_prefix": "/alternate"}


def _settings(source, tmp_path, monkeypatch):
    if source == "init":
        return AppConfig(_env_file=None, http=FIELDS)
    if source == "env":
        for field, value in FIELDS.items():
            monkeypatch.setenv("KODEZART_HTTP__" + field.upper(), str(value))
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(
            "\n".join(
                f"KODEZART_HTTP__{field.upper()}='{value}'"
                for field, value in FIELDS.items()
            )
        )
        return AppConfig(_env_file=path)
    (tmp_path / "KODEZART_HTTP").write_text(json.dumps(FIELDS))
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
async def test_supported_http_sources_drive_the_actual_application(
    source, tmp_path, monkeypatch
):
    config = _settings(source, tmp_path, monkeypatch)
    assert config.http.model_dump() == FIELDS
    monkeypatch.setattr(AppConfig, "from_env", lambda: config)
    app = create_app()
    assert app.title == "Fixture HTTP"
    assert app.debug is True
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/redoc")).status_code == 200
        schema = (await client.get("/openapi.json")).json()
        assert schema["info"]["title"] == "Fixture HTTP"
        assert "/alternate/agent/fire" in schema["paths"]
        assert (await client.post("/api/v1/agent/fire", json=BODY)).status_code == 404


async def test_default_http_settings_preserve_title_routes_and_hidden_docs():
    app = create_app()
    assert app.title == "kodezart" and app.debug is False
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/redoc")).status_code == 404
        schema = (await client.get("/openapi.json")).json()
        assert "/api/v1/agent/fire" in schema["paths"]


@pytest.mark.parametrize("endpoint", ["fire", "workflow"])
async def test_configured_prefix_reaches_json_and_sse_job_handles(
    endpoint, monkeypatch
):
    monkeypatch.setenv("KODEZART_HTTP__API_V1_PREFIX", "/deployed/v1")
    app = create_app()
    frame = AssistantTextEvent(text="frame", model="fixture")
    queue = FakeJobQueue(events=[frame])
    app.dependency_overrides.update(
        {
            deps.get_agent_runner: lambda: FakeAgentRunner(events=[frame]),
            deps.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            deps.get_job_queue: lambda: queue,
        }
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/deployed/v1/agent/{endpoint}", json=BODY)
    assert response.status_code == (202 if endpoint == "fire" else 200)
    handle = response.json() if endpoint == "fire" else events(response)[0]
    assert handle["statusUrl"] == "/deployed/v1/jobs/job-0001"
    assert handle["streamUrl"] == "/deployed/v1/jobs/job-0001/stream"


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize(
    ("field", "value"),
    [("project_name", "fixture"), ("debug", "true"), ("api_v1_prefix", "/prefix")],
)
def test_old_flat_http_names_refuse(source, field, value, tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, value, tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


def test_http_source_precedence_is_unchanged(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_HTTP").write_text(json.dumps(FIELDS))
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_HTTP__PROJECT_NAME=dotenv\n")
    monkeypatch.setenv("KODEZART_HTTP__PROJECT_NAME", "environment")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert (
        AppConfig(**kwargs, http={"project_name": "init"}).http.project_name == "init"
    )
    assert AppConfig(**kwargs).http.project_name == "environment"
    monkeypatch.delenv("KODEZART_HTTP__PROJECT_NAME")
    assert AppConfig(**kwargs).http.project_name == "dotenv"
    assert AppConfig(_env_file=None, _secrets_dir=secrets).http.model_dump() == FIELDS


@pytest.mark.parametrize("field,value", [("debug", "invalid"), ("debugg", True)])
def test_invalid_nested_http_configuration_refuses(field, value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, http={field: value})
