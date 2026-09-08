"""The same tracked identity addresses session prose and runner properties."""

import pytest
from pydantic import SecretStr

from kodezart.composition.knowledge import fire_record_template
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.operation import OperationConfig, RunKind
from kodezart.types.domain.run_records import RunRecordResult
from kodezart.types.domain.session import SessionType
from tests.fakes import EXECUTOR_MODULES, knowledge_grant_for, recorded_session
from tests.probes.notion_records import NotionLogServer
from tests.prompts.test_knowledge_map import example_config
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_run_recorder import _record
from tests.services.test_structured_run_records import destination, recorder


def operation():
    original = example_config().model_dump()
    original["records"]["fire"] = destination().model_dump()
    return OperationConfig.model_validate(original)


def template(set_name="claude-opus", *, configured=True):
    config = operation()
    prompts = load_registry(default_set=set_name, bindings=dict(bindings_for(config)))
    return fire_record_template(
        config=AppConfig(
            knowledge_session_grants=(SessionType.TICKET_FIRE,),
            knowledge_mcp_token=SecretStr("ntn_" + "F" * 44),
            knowledge_mcp_server_url="https://knowledge.invalid/mcp",
        ),
        operation=config if configured else example_config(),
        prompts=prompts,
    )


@pytest.mark.parametrize("set_name", ["claude-opus", "anthropic_v5"])
@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_granted_tracked_fire_gets_its_own_exact_record_clause(module, set_name):
    record = _record(RunKind.FIRE)
    session = await recorded_session(
        module,
        grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        session_type=SessionType.TICKET_FIRE,
        prompt="The implementation task.",
        fire_record=template(set_name),
        run_identity=record.identity(),
    )
    assert "The implementation task." in session.prompt
    assert f"title is exactly:\n\n{record.title()}\n" in session.prompt
    assert "Fixture Log" in session.prompt and "destination-1" in session.prompt
    assert "What happened" in session.prompt
    assert "honest account" in session.prompt
    assert "Preserve\nearlier observations" in session.prompt
    assert "{{" not in session.prompt


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(
    "absence", ["grant", "identity", "destination", "fire_session"]
)
async def test_unrelated_or_unconfigured_sessions_do_not_acquire_a_record_contract(
    module, absence
):
    record = _record(RunKind.FIRE)
    session_type = (
        SessionType.API_QUERY if absence == "fire_session" else SessionType.TICKET_FIRE
    )
    grant = knowledge_grant_for(*(() if absence == "grant" else (session_type,)))
    session = await recorded_session(
        module,
        grant=grant,
        session_type=session_type,
        prompt="Original task.",
        fire_record=template(configured=absence != "destination"),
        run_identity=None if absence == "identity" else record.identity(),
    )
    assert "Original task." in session.prompt
    assert record.title() not in session.prompt
    assert "honest account" not in session.prompt


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_session_authored_narrative_and_terminal_backfill_share_one_row(module):
    """The fake knowledge store performs the session's requested write once."""
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    session = await recorded_session(
        module,
        grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        session_type=SessionType.TICKET_FIRE,
        fire_record=template(),
        run_identity=record.identity(),
    )
    rendered_title = session.prompt.split("title is exactly:\n\n", 1)[1].split("\n", 1)[
        0
    ]
    narrative = "I ran the parser probe. It failed; the edge remains unresolved."
    await server.call_tool(
        name="API-post-page",
        arguments={
            "parent": {"type": "data_source_id", "data_source_id": destination().id},
            "properties": {
                "Run": {"title": [{"text": {"content": rendered_title}}]},
                "What happened": {"rich_text": [{"text": {"content": narrative}}]},
            },
        },
    )
    service = recorder(server, destination())
    assert await service.record(record) is RunRecordResult.WRITTEN
    assert await service.record(record) is RunRecordResult.VERIFIED
    assert len(server.rows) == 1
    properties = next(iter(server.rows.values()))["properties"]
    assert properties["What happened"]["rich_text"] == [{"plain_text": narrative}]
    assert properties["Run"]["title"] == [{"plain_text": record.title()}]
    assert [name for name, _ in server.writes()] == ["API-post-page", "API-patch-page"]


@pytest.mark.parametrize("microsecond", [100000, 200000])
async def test_fractional_start_identity_is_shared_by_the_clause_and_runner(
    microsecond,
):
    record = _record(RunKind.FIRE)
    record = record.model_copy(
        update={"started_at": record.started_at.replace(microsecond=microsecond)}
    )
    session = await recorded_session(
        EXECUTOR_MODULES[0],
        grant=knowledge_grant_for(SessionType.TICKET_FIRE),
        session_type=SessionType.TICKET_FIRE,
        fire_record=template(),
        run_identity=record.identity(),
    )
    assert f"title is exactly:\n\n{record.title()}\n" in session.prompt
    assert f".{microsecond:06d}Z" in record.title()


def test_a_naive_timestamp_cannot_be_mislabeled_as_utc():
    from datetime import datetime

    from pydantic import ValidationError

    from kodezart.types.domain.run_records import RunIdentity

    with pytest.raises(ValidationError, match="timezone"):
        RunIdentity(kind=RunKind.FIRE, name="EX-42", started_at=datetime(2026, 9, 1))
