"""Prompt for agent-generated pull request descriptions."""

from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import TicketDraftOutput


def build_prompt(
    *,
    ticket: TicketDraftOutput,
    acceptance_criteria: list[str],
    total_iterations: int,
) -> str:
    """Build a prompt that instructs the agent to write a PR description."""
    task_markdown = format_ticket_as_task(ticket)
    criteria_list = "\n".join(f"- {c}" for c in acceptance_criteria)

    return (
        "Write a pull request title and description for the following "
        "implementation work.\n\n"
        f"## Ticket\n{task_markdown}\n\n"
        f"## Acceptance Criteria\n{criteria_list}\n\n"
        f"## Implementation Stats\n"
        f"- Total iterations: {total_iterations}\n\n"
        "## Instructions\n"
        "1. Write a concise PR title (max 120 characters).\n"
        "2. Write a markdown description with:\n"
        "   - A one-paragraph summary of the changes.\n"
        "   - A bulleted list of key changes.\n"
        "   - A brief verification method (how to test).\n"
        "3. End the description with this footer:\n\n"
        "```\n"
        "---\n"
        "_Automated by kodezart_\n"
        "```\n\n"
        "Output structured JSON with `title` and `description` fields."
    )
