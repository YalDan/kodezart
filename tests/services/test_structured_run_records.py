"""Actual Notion property writes use explicitly selected outcome semantics."""

import asyncio
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.core.errors import RunRecordWriteError
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import (
    DocumentSystem,
    RecordColumns,
    RecordDestination,
    RecordDurationUnit,
    RecordOutcomeMapping,
    RunKind,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome, RunRecordResult
from tests.probes.notion_records import NotionLogServer
from tests.services.test_run_recorder import _record


def destination(*, options=None):
    return RecordDestination(
        system=DocumentSystem.KNOWLEDGE,
        name="Fixture Log",
        id="destination-1",
        append_only=True,
        columns=RecordColumns(
            repo="Repository",
            pr_url="Pull request",
            base_branch="Base",
            started="Began",
            ended="Ended",
            duration="Minutes",
            duration_unit=RecordDurationUnit.MINUTES,
            iterations="Iterations",
            what_happened="What happened",
            repo_options={"https://forge.invalid/owner/repo": "owner/repo"},
        ),
        outcome_mapping=RecordOutcomeMapping(
            property="Disposition",
            options={"run.completed": "Finished", "run.failed": "Failed"}
            if options is None
            else options,
        ),
    )


def recorder(server, target):
    return RunRecorder(
        records={RunKind.FIRE.value: target},
        sinks={
            DocumentSystem.KNOWLEDGE: NotionRecordSink(
                caller=server, server_name="fixture"
            )
        },
    )


@pytest.mark.parametrize(
    "outcome,option",
    [(RunOutcome.COMPLETED, "Finished"), (RunOutcome.FAILED, "Failed")],
)
async def test_known_runner_outcome_reaches_its_declared_select(outcome, option):
    server = NotionLogServer()
    record = _record(RunKind.FIRE).model_copy(update={"outcome": outcome})
    service = recorder(server, destination())
    assert await service.record(record) is RunRecordResult.WRITTEN
    row = next(iter(server.rows.values()))
    assert row["properties"]["Run"]["title"] == [{"plain_text": record.title()}]
    assert row["properties"]["Disposition"] == {"select": {"name": option}}
    assert await service.record(record) is RunRecordResult.VERIFIED
    assert len(server.rows) == len(server.writes()) == 1


async def test_partial_session_row_is_completed_in_place_and_judgment_is_preserved():
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    narrative = {"rich_text": [{"plain_text": "I investigated the failed probe."}]}
    key = server.seed(record.title(), properties={"What happened": narrative})
    await recorder(server, destination()).record(record)
    assert list(server.rows) == [key]
    assert [call[0] for call in server.writes()] == ["API-patch-page"]
    assert server.rows[key]["properties"]["What happened"] == narrative
    assert server.rows[key]["properties"]["Disposition"] == {
        "select": {"name": "Finished"}
    }


async def test_workflow_outcome_is_used_only_when_explicitly_selected():
    record = _record(RunKind.FIRE).model_copy(
        update={"workflow_outcome": WorkflowOutcome.pr_opened}
    )
    for options, expected in [
        ({"run.completed": "Finished"}, "Finished"),
        ({"workflow.pr_opened": "PR opened"}, "PR opened"),
    ]:
        server = NotionLogServer()
        await recorder(server, destination(options=options)).record(record)
        row = next(iter(server.rows.values()))
        assert row["properties"]["Disposition"]["select"]["name"] == expected


@pytest.mark.parametrize(
    "defect",
    [
        "missing_mapping",
        "missing_value",
        "absent_option",
        "wrong_property",
        "missing_source_fact",
    ],
)
async def test_unmappable_outcomes_refuse_before_any_record_mutation(defect):
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    target = destination()
    if defect == "missing_mapping":
        target = target.model_copy(update={"outcome_mapping": None})
    elif defect == "missing_value":
        target = destination(options={})
    elif defect == "absent_option":
        target = destination(options={"run.completed": "unknown option"})
    elif defect == "wrong_property":
        server.schema["properties"]["Disposition"] = {"type": "rich_text"}
    else:
        target = destination(options={"workflow.pr_opened": "PR opened"})
    server.seed(record.title())
    with pytest.raises(RunRecordWriteError) as caught:
        await recorder(server, target).record(record)
    assert caught.value.failure == "mapping_invalid"
    assert caught.value.destination == target.id
    assert server.writes() == []


async def test_destination_option_changes_are_checked_again_before_a_write():
    server = NotionLogServer()
    service = recorder(server, destination())
    record = _record(RunKind.FIRE)
    await service.record(record)
    server.schema["properties"]["Disposition"]["select"]["options"] = []
    with pytest.raises(RunRecordWriteError):
        await service.record(record)
    assert len(server.writes()) == 1


async def test_duplicate_identity_rows_refuse_instead_of_picking_one():
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    server.seed(record.title())
    server.seed(record.title())
    with pytest.raises(RunRecordWriteError) as caught:
        await recorder(server, destination()).record(record)
    assert caught.value.failure == "identity_conflict"
    assert server.writes() == []


async def test_conflicting_observed_mappings_refuse_without_precedence():
    server = NotionLogServer()
    record = _record(RunKind.FIRE).model_copy(
        update={"workflow_outcome": WorkflowOutcome.pr_opened}
    )
    target = destination(
        options={"run.completed": "Finished", "workflow.pr_opened": "PR opened"}
    )
    with pytest.raises(RunRecordWriteError):
        await recorder(server, target).record(record)
    assert server.writes() == []


@pytest.mark.parametrize(
    "workflow, runner, expected",
    [
        (WorkflowOutcome.pr_opened, RunOutcome.COMPLETED, "PR opened"),
        (None, RunOutcome.FAILED, "Failed"),
    ],
)
async def test_fine_terminal_outcomes_and_preterminal_failures_share_explicit_mapping(
    workflow, runner, expected
):
    server = NotionLogServer()
    record = _record(RunKind.FIRE).model_copy(
        update={"workflow_outcome": workflow, "outcome": runner}
    )
    target = destination(
        options={"workflow.pr_opened": "PR opened", "run.failed": "Failed"}
    )
    await recorder(server, target).record(record)
    row = next(iter(server.rows.values()))
    assert row["properties"]["Disposition"]["select"]["name"] == expected


@pytest.mark.parametrize(
    "key,option",
    [
        ("completed", "Finished"),
        ("other.completed", "Finished"),
        ("run.", "Finished"),
        ("run.completed", " "),
    ],
)
def test_outcome_mapping_requires_explicit_sources_and_nonempty_options(key, option):
    with pytest.raises(ValidationError):
        RecordOutcomeMapping(property="Disposition", options={key: option})


async def test_legacy_structural_title_is_normalized_without_a_second_row():
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    key = server.seed(
        record.line(), properties={"Disposition": {"select": {"name": "Finished"}}}
    )
    await recorder(server, destination()).record(record)
    assert list(server.rows) == [key]
    assert server.rows[key]["properties"]["Run"]["title"] == [
        {"plain_text": record.title()}
    ]


@pytest.mark.parametrize(
    "missing",
    ["Repository", "Base", "Began", "Ended", "Minutes", "Iterations", "What happened"],
)
async def test_declared_column_schema_is_checked_even_before_a_fact_is_known(missing):
    server = NotionLogServer()
    del server.schema["properties"][missing]
    with pytest.raises(RunRecordWriteError) as caught:
        await recorder(server, destination()).record(_record(RunKind.FIRE))
    assert caught.value.failure == "mapping_invalid"
    assert server.writes() == []


async def test_unknown_repository_mapping_refuses_without_guessing_an_alias():
    from kodezart.types.domain.run_records import FireRecordFacts

    server = NotionLogServer()
    record = _record(RunKind.FIRE).model_copy(
        update={
            "fire_facts": FireRecordFacts(repo_url="https://forge.invalid/another/repo")
        }
    )
    with pytest.raises(RunRecordWriteError):
        await recorder(server, destination()).record(record)
    assert server.writes() == []


async def test_unknown_facts_do_not_erase_session_fields_and_zero_is_written():
    from kodezart.types.domain.run_records import FireRecordFacts

    server = NotionLogServer()
    record = _record(RunKind.FIRE).model_copy(
        update={"fire_facts": FireRecordFacts(iterations=0)}
    )
    session_base = {"rich_text": [{"plain_text": "observed/session/base"}]}
    key = server.seed(record.title(), properties={"Base": session_base})
    await recorder(server, destination()).record(record)
    assert server.rows[key]["properties"]["Base"] == session_base
    assert server.rows[key]["properties"]["Iterations"]["number"] == 0
    assert "What happened" not in server.rows[key]["properties"]


async def test_remaining_known_facts_are_filled_even_when_outcome_is_already_correct():
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    key = server.seed(
        record.title(), properties={"Disposition": {"select": {"name": "Finished"}}}
    )
    await recorder(server, destination()).record(record)
    assert server.rows[key]["properties"]["Minutes"]["number"] == pytest.approx(
        record.duration_seconds / 60
    )
    assert len(server.rows) == 1
    assert server.writes()[0][0] == "API-patch-page"


async def test_identity_lookup_visits_later_pages_before_creating():
    server = NotionLogServer()
    record = _record(RunKind.FIRE)
    for number in range(101):
        server.seed(f"{record.title()}?unrelated-{number}")
    key = server.seed(record.title())
    await recorder(server, destination()).record(record)
    assert len(server.rows) == 102
    assert server.writes()[0][0] == "API-patch-page"
    assert server.writes()[0][1]["page_id"] == key


@pytest.mark.parametrize("cursor", [None, "opaque:next/page"])
async def test_unfinished_record_query_never_becomes_permission_to_create(cursor):
    class Unfinished(NotionLogServer):
        async def call_tool(self, *, name, arguments):
            if name != "API-query-data-source":
                return await super().call_tool(name=name, arguments=arguments)
            queries = [call for call in self.calls if call[0] == name]
            assert len(queries) < 2, "A stuck cursor caused another remote request"
            self.calls.append((name, dict(arguments)))
            return {
                "results": [],
                "has_more": True,
                "next_cursor": "opaque:next/page" if not queries else cursor,
            }

    server = Unfinished()
    with pytest.raises(RunRecordWriteError, match="pagination cannot advance"):
        await recorder(server, destination()).record(_record(RunKind.FIRE))
    assert server.writes() == []
    queries = [args for name, args in server.calls if name == "API-query-data-source"]
    assert "start_cursor" not in queries[0]
    assert queries[1] == {**queries[0], "start_cursor": "opaque:next/page"}


async def test_final_record_page_ignores_its_leftover_cursor():
    class FinalCursor(NotionLogServer):
        async def call_tool(self, *, name, arguments):
            result = await super().call_tool(name=name, arguments=arguments)
            if name == "API-query-data-source":
                assert result["has_more"] is False
                result["next_cursor"] = "opaque:unused"
            return result

    server = FinalCursor()
    record = _record(RunKind.FIRE)
    existing = server.seed(record.title())
    await recorder(server, destination()).record(record)
    assert len(server.rows) == 1
    assert server.writes()[0][1]["page_id"] == existing
    assert all(
        "start_cursor" not in arguments
        for name, arguments in server.calls
        if name == "API-query-data-source"
    )


async def test_cancelled_record_query_restarts_with_its_own_cursor_history():
    class Paused(NotionLogServer):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.resume = asyncio.Event()
            self.pause = True

        async def call_tool(self, *, name, arguments):
            if name != "API-query-data-source":
                return await super().call_tool(name=name, arguments=arguments)
            self.calls.append((name, dict(arguments)))
            if "start_cursor" not in arguments:
                return {"results": [], "has_more": True, "next_cursor": "opaque"}
            assert arguments["start_cursor"] == "opaque"
            if self.pause:
                self.entered.set()
                await self.resume.wait()
            return {
                "results": list(self.rows.values()),
                "has_more": False,
                "next_cursor": "opaque",
            }

    server = Paused()
    record = _record(RunKind.FIRE)
    existing = server.seed(record.title())
    service = recorder(server, destination())
    task = asyncio.create_task(service.record(record))
    try:
        async with asyncio.timeout(2):
            await server.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert server.writes() == []
    finally:
        task.cancel()
        server.resume.set()
        await asyncio.gather(task, return_exceptions=True)
    server.pause = False
    await service.record(record)
    assert len(server.rows) == 1
    assert server.writes()[0][1]["page_id"] == existing


async def test_duration_unit_is_explicit_and_seconds_are_not_converted():
    server = NotionLogServer()
    target = destination()
    target = target.model_copy(
        update={
            "columns": target.columns.model_copy(
                update={"duration_unit": RecordDurationUnit.SECONDS}
            )
        }
    )
    record = _record(RunKind.FIRE)
    await recorder(server, target).record(record)
    properties = next(iter(server.rows.values()))["properties"]
    assert properties["Minutes"]["number"] == record.duration_seconds


async def test_two_same_issue_fires_in_one_second_remain_distinct_rows():
    server = NotionLogServer()
    first = _record(RunKind.FIRE)
    first = first.model_copy(
        update={"started_at": first.started_at.replace(microsecond=100000)}
    )
    second = first.model_copy(
        update={"started_at": first.started_at.replace(microsecond=200000)}
    )
    service = recorder(server, destination())
    assert await service.record(first) is RunRecordResult.WRITTEN
    assert await service.record(second) is RunRecordResult.WRITTEN
    assert len(server.rows) == 2
    assert first.title() != second.title()
    assert await service.record(first) is RunRecordResult.VERIFIED
    assert await service.record(second) is RunRecordResult.VERIFIED


async def test_same_instant_in_another_timezone_is_the_same_record_identity():
    server = NotionLogServer()
    original = _record(RunKind.FIRE)
    offset = original.model_copy(
        update={
            "started_at": original.started_at.astimezone(timezone(timedelta(hours=2))),
            "recorded_at": original.recorded_at.astimezone(
                timezone(timedelta(hours=2))
            ),
        }
    )
    service = recorder(server, destination())
    assert offset.title() == original.title()
    assert await service.record(original) is RunRecordResult.WRITTEN
    assert await service.record(offset) is RunRecordResult.VERIFIED
    assert len(server.rows) == 1
