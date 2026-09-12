"""Pure replay of explicitly recorded own mutations against retained content."""

import json

from kodezart.types.domain.self_writes import IssueMovementSnapshot, OwnMutation


def matches_own_mutations(
    *,
    before: IssueMovementSnapshot,
    after: IssueMovementSnapshot,
    mutations: tuple[OwnMutation, ...],
) -> bool:
    """Whether replaying nonempty receipts explains the observed content.

    Timestamp-only changes with no new receipt remain movement. Histories
    that the native reads cannot distinguish are not proof of event order.
    """
    if before.issue_key != after.issue_key or not mutations:
        return False
    fields = dict(before.fields)
    comments = dict(before.comments)
    for mutation in mutations:
        if any(fields.get(key) != value for key, value in mutation.expected_fields):
            return False
        fields.update(mutation.fields)
        for key, additions in mutation.additions:
            if key not in fields:
                return False
            held = json.loads(fields[key])
            if not isinstance(held, list):
                return False
            for encoded in additions:
                value = json.loads(encoded)
                if value not in held:
                    held.append(value)
            fields[key] = json.dumps(held, sort_keys=True, separators=(",", ":"))
        for key, values in mutation.created:
            if key in comments:
                return False
            comments[key] = values
        for key, values in mutation.edited:
            if key not in comments:
                return False
            comments[key] = tuple(
                sorted({**dict(comments[key]), **dict(values)}.items())
            )
        for key in mutation.deleted:
            if key not in comments:
                return False
            del comments[key]
    return (
        tuple(sorted(fields.items())) == after.fields
        and tuple(sorted(comments.items())) == after.comments
    )
