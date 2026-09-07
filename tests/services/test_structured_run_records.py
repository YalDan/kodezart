"""Actual Notion property writes use explicitly selected outcome semantics."""

import pytest
from pydantic import ValidationError

from kodezart.adapters.notion_record_sink import NotionRecordSink
from kodezart.core.errors import RunRecordWriteError
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.operation import (
    DocumentSystem,
    RecordDestination,
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
