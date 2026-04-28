"""Ticket rendering — pure domain formatter."""

from kodezart.types.domain.agent import TicketDraftOutput


def format_ticket_as_task(ticket: TicketDraftOutput) -> str:
    """Render a TicketDraftOutput to deterministic markdown."""
    sections: list[str] = [
        f"# {ticket.title}",
        f"## Summary\n{ticket.summary}",
        f"## Context\n{ticket.context}",
    ]

    if ticket.references:
        refs = "\n".join(
            f"- `{ref.location}` — {ref.note}" for ref in ticket.references
        )
        sections.append(f"## References\n{refs}")

    changes = "\n\n".join(
        f"### {change.change_type}: {change.file_path}\n"
        f"{change.description}\n{change.rationale}"
        for change in ticket.required_changes
    )
    sections.append(f"## Required Changes\n{changes}")

    if ticket.out_of_scope:
        items = "\n".join(f"- {item}" for item in ticket.out_of_scope)
        sections.append(f"## Out of Scope\n{items}")

    if ticket.open_questions:
        items = "\n".join(f"- {item}" for item in ticket.open_questions)
        sections.append(f"## Open Questions\n{items}")

    return "\n\n".join(sections)
