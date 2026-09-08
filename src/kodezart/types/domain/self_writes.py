"""Retained native observations and explicit deterministic write receipts.

These values are process-local bookkeeping, not an admission digest or an
actor/causality claim. Field names and serialized values are adapter-owned;
consumers only replay the declared mutations and compare the whole result.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

type FieldValues = tuple[tuple[str, str], ...]
type CommentValues = tuple[tuple[str, FieldValues], ...]


def field_value(value: object) -> str:
    """Retain one native JSON value as an immutable, deterministic string."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def field_values(values: Mapping[str, object]) -> FieldValues:
    """Freeze exact JSON values without interpreting backend field names."""
    return tuple((key, field_value(value)) for key, value in sorted(values.items()))


@dataclass(frozen=True)
class IssueMovementSnapshot:
    """A stable issue and complete comment log, separate from its scan stamp."""

    issue_key: str
    updated_at: datetime
    fields: FieldValues
    comments: CommentValues


@dataclass(frozen=True)
class OwnMutation:
    """Only fields actually written, never an arbitrary post-write snapshot."""

    fields: FieldValues = ()
    additions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    created: CommentValues = ()
    edited: CommentValues = ()
    deleted: tuple[str, ...] = ()
