"""Attribute only one observed native state interval to an atomic state write."""

from collections.abc import Mapping
from datetime import datetime

from pydantic import ValidationError

from kodezart.adapters.linear_mcp_types import LinearStateHistoryEntryWire
from kodezart.types.domain.self_writes import OwnMutation, field_value, field_values


def state_history_receipt(
    *,
    before: object,
    after: object,
    previous_state: str,
    previous_type: str,
    written_state: str,
    written_type: str,
    before_stamp: datetime,
    write_stamp: datetime,
) -> OwnMutation | None:
    """A guarded replacement; any other history change remains unaccounted for.

    The adapter separately requires the full read's stamp to equal the atomic
    write response. Historical rows survive byte-for-byte, except that the one
    previously open interval closes where the new interval begins.
    """
    if not isinstance(before, list) or not isinstance(after, list):
        return None
    if before_stamp.utcoffset() is None or write_stamp.utcoffset() is None:
        return None
    if not all(isinstance(row, Mapping) for row in [*before, *after]):
        return None
    try:
        old = [LinearStateHistoryEntryWire.model_validate(row) for row in before]
        new = [LinearStateHistoryEntryWire.model_validate(row) for row in after]
    except ValidationError:
        return None
    opened = [index for index, row in enumerate(old) if row.ended_at is None]
    current = [index for index, row in enumerate(new) if row.ended_at is None]
    if len(opened) != 1 or len(current) != 1 or len(new) != len(old) + 1:
        return None
    old_index, new_index = opened[0], current[0]
    entry = new[new_index]
    if (
        old[old_index].state.name != previous_state
        or old[old_index].state.type != previous_type
        or entry.state.name != written_state
        or entry.state.type != written_type
        or not before_stamp <= entry.started_at <= write_stamp
    ):
        return None
    closed = dict(before[old_index])
    closed["endedAt"] = after[new_index]["startedAt"]
    expected = [
        closed if index == old_index else row for index, row in enumerate(before)
    ]
    expected.append(after[new_index])
    if sorted(map(field_value, expected)) != sorted(map(field_value, after)):
        return None
    return OwnMutation(
        fields=field_values({"stateHistory": after}),
        expected_fields=field_values({"stateHistory": before}),
    )
