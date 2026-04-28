"""Shared constants for workflow execution."""

EVAL_PERMISSION_MODE = "plan"
EVAL_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash"]
EVAL_TOOLS_WITH_AGENT: list[str] = [*EVAL_TOOLS, "Agent"]
TICKET_TOOLS: list[str] = [*EVAL_TOOLS, "Agent", "WebSearch", "WebFetch"]
