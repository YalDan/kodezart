"""Label contracts observed through the connected app on 2026-09-07.

This is NOT a fresh capture using the operation's service credential.
That credential was unavailable. In particular, ``save_project_label``
was absent from the 2026-08-25 service roster; service-authenticated boot
still needs verification. These declarations establish a real public
contract for implementation and synthetic conformance, not deployment
compatibility. No create calls or entity assignments were performed.

Read envelopes below are captures, with identifiers replaced by fixture
identifiers. The project response was empty: neither a populated project
entry nor a create-response shape was measured by this exercise.
"""

from collections.abc import Mapping

CONNECTED_APP_LABEL_ARGUMENTS: Mapping[str, frozenset[str]] = {
    "list_issue_labels": frozenset({"cursor", "limit", "name", "orderBy", "team"}),
    "list_project_labels": frozenset({"cursor", "limit", "name", "orderBy"}),
    "list_initiative_labels": frozenset({"cursor", "limit", "name", "orderBy"}),
    "create_issue_label": frozenset(
        {"name", "color", "description", "isGroup", "parent", "teamId"},
    ),
    "create_initiative_label": frozenset(
        {"name", "color", "description", "isGroup", "parent"},
    ),
    "save_project_label": frozenset(
        {"id", "name", "color", "description", "isGroup", "parent"},
    ),
}

CONNECTED_APP_LABEL_REQUIRED: Mapping[str, frozenset[str]] = {
    "list_issue_labels": frozenset(),
    "list_project_labels": frozenset(),
    "list_initiative_labels": frozenset(),
    "create_issue_label": frozenset({"name"}),
    "create_initiative_label": frozenset({"name"}),
    "save_project_label": frozenset(),
}

CONNECTED_APP_LABEL_TOOLS = frozenset(CONNECTED_APP_LABEL_ARGUMENTS)

CONNECTED_ISSUE_LABELS: Mapping[str, object] = {
    "labels": [
        {
            "id": "captured-issue-label",
            "name": "scope:approved",
            "color": "#f7c8c1",
            "description": None,
        },
    ],
    "hasNextPage": False,
}

CONNECTED_INITIATIVE_LABELS: Mapping[str, object] = {
    "labels": [
        {
            "id": "captured-initiative-label",
            "name": "scope:approved",
            "color": "#f7c8c1",
            "description": "sanitized captured description",
        },
    ],
    "hasNextPage": False,
}

CONNECTED_PROJECT_LABELS: Mapping[str, object] = {
    "labels": [],
    "hasNextPage": False,
}
