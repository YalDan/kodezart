"""Configured run outcome semantics resolved against a destination schema."""

from kodezart.core.errors import RunRecordWriteError
from kodezart.types.domain.notion_records import (
    NotionDateValue,
    NotionPropertyValue,
    NotionReadText,
    NotionRecordSchema,
    NotionSelectOption,
)
from kodezart.types.domain.operation import (
    RecordDestination,
    RecordDurationUnit,
    RunKind,
)
from kodezart.types.domain.run_records import RunRecord, RunRecordFailure


def mapping_error(
    destination: RecordDestination, record: RunRecord, reason: str
) -> RunRecordWriteError:
    return RunRecordWriteError(
        reason,
        kind=record.kind.value,
        destination=destination.id,
        system=destination.system.value,
        failure=RunRecordFailure.MAPPING_INVALID.value,
    )


def mapped_outcome(
    destination: RecordDestination, record: RunRecord, schema: NotionRecordSchema
) -> tuple[str, str]:
    """The configured column and option, with no cross-vocabulary inference."""
    mapping = destination.outcome_mapping
    if mapping is None:
        raise mapping_error(
            destination, record, "the fire has no declared outcome mapping"
        )
    observed = {f"run.{record.outcome.value}"}
    if record.workflow_outcome is not None:
        observed.add(f"workflow.{record.workflow_outcome.value}")
    options = {option for key, option in mapping.options.items() if key in observed}
    if len(options) != 1:
        raise mapping_error(
            destination,
            record,
            "observed outcomes require exactly one configured destination option: "
            + ", ".join(sorted(observed)),
        )
    option = next(iter(options))
    definition = schema.properties.get(mapping.property)
    if definition is None or definition.type != "select" or definition.select is None:
        raise mapping_error(
            destination,
            record,
            f"outcome property {mapping.property!r} is not a select",
        )
    if option not in {entry.name for entry in definition.select.options}:
        raise mapping_error(
            destination,
            record,
            f"outcome option {option!r} is absent from the destination",
        )
    return mapping.property, option


def record_properties(
    destination: RecordDestination,
    record: RunRecord,
    schema: NotionRecordSchema,
    title: str,
) -> dict[str, NotionPropertyValue]:
    """Known facts only, each bound to a declared and validated column."""
    column, option = mapped_outcome(destination, record, schema)
    properties = {
        title: NotionPropertyValue(title=[NotionReadText(plain_text=record.title())]),
        column: NotionPropertyValue(select=NotionSelectOption(name=option)),
    }
    columns = destination.columns
    if columns is None:
        if record.kind is RunKind.FIRE:
            raise mapping_error(
                destination, record, "the fire has no declared record columns"
            )
        return properties
    for name, kind in (
        (columns.repo, "select"),
        (columns.pr_url, "url"),
        (columns.base_branch, "rich_text"),
        (columns.started, "date"),
        (columns.ended, "date"),
        (columns.duration, "number"),
        (columns.iterations, "number"),
        (columns.what_happened, "rich_text"),
    ):
        definition = schema.properties.get(name)
        if definition is None or definition.type != kind:
            raise mapping_error(
                destination, record, f"record property {name!r} must be {kind}"
            )
    facts = record.fire_facts
    if facts.repo_url is not None:
        repo_option = columns.repo_options.get(facts.repo_url)
        definition = schema.properties[columns.repo]
        if (
            repo_option is None
            or definition.select is None
            or repo_option not in {entry.name for entry in definition.select.options}
        ):
            raise mapping_error(
                destination,
                record,
                "the observed repository has no valid destination option",
            )
        properties[columns.repo] = NotionPropertyValue(
            select=NotionSelectOption(name=repo_option)
        )
    if facts.pr_url is not None:
        properties[columns.pr_url] = NotionPropertyValue(url=facts.pr_url)
    if facts.base_branch is not None:
        properties[columns.base_branch] = NotionPropertyValue(
            rich_text=[NotionReadText(plain_text=facts.base_branch)]
        )
    if facts.iterations is not None:
        properties[columns.iterations] = NotionPropertyValue(number=facts.iterations)
    properties[columns.started] = NotionPropertyValue(
        date=NotionDateValue(start=record.started_at)
    )
    properties[columns.ended] = NotionPropertyValue(
        date=NotionDateValue(start=record.recorded_at)
    )
    seconds_per_unit = 60 if columns.duration_unit is RecordDurationUnit.MINUTES else 1
    properties[columns.duration] = NotionPropertyValue(
        number=record.duration_seconds / seconds_per_unit
    )
    return properties


def write_properties(properties: dict[str, NotionPropertyValue]) -> dict[str, object]:
    """Notion's write rich text uses text.content; its read uses plain_text."""
    result: dict[str, object] = {}
    for name, value in properties.items():
        payload = value.model_dump(mode="json", exclude_none=True)
        for kind, parts in (("title", value.title), ("rich_text", value.rich_text)):
            if parts is not None:
                payload[kind] = [
                    {"text": {"content": part.plain_text}} for part in parts
                ]
        result[name] = payload
    return result
