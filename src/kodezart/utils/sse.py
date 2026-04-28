"""SSE formatting utilities for event-stream responses."""

import json


def format_sse(event: dict[str, object]) -> str:
    """Format a dictionary as an SSE frame.

    Uses the ``type`` key as the event name (defaulting to ``message``) and
    JSON-serializes the full dict as the data payload.  Output format:
    ``event: {type}\\ndata: {json}\\n\\n``
    """
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
