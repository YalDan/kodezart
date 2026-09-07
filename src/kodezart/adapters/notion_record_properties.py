"""Configured run outcome semantics resolved against a destination schema."""

from kodezart.core.errors import RunRecordWriteError
from kodezart.types.domain.notion_records import NotionRecordSchema
from kodezart.types.domain.operation import RecordDestination
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
