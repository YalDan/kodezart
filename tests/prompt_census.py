"""The explicit prompt-role census, independent of the enum under test."""

from typing import Final

PROMPT_FUNCTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "branch_name",
        "ticket_create",
        "ticket_review",
        "ticket_revision",
        "acceptance_criteria",
        "criteria_validation",
        "implementation",
        "evaluation",
        "iteration_feedback",
        "post_merge_review",
        "fix",
        "remediation_ticket",
        "commit_message",
        "pr_description",
        "fire_prep_pass",
        "grooming_pass",
        "content_audit",
        "knowledge_map",
        "organize_assess",
        "organize_author",
        "organize_verify",
        "organize_criteria_author",
    }
)


def configured_investigation_cap() -> int:
    """The shipped fan-out cap, read off the field declaration.

    Off the DECLARATION rather than a constructed config: a suite that
    builds ``AppConfig()`` to learn the default asks the ambient
    environment what the application ships, and the answer changes with
    whoever exported a variable last.
    """
    from kodezart.core.config import AppConfig

    return int(AppConfig.model_fields["investigation_cap"].default)
