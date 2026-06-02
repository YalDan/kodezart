"""Shared constants for workflow execution."""

EVAL_PERMISSION_MODE = "plan"
EVAL_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash"]
EVAL_TOOLS_WITH_AGENT: list[str] = [*EVAL_TOOLS, "Agent"]
TICKET_TOOLS: list[str] = [*EVAL_TOOLS, "Agent", "WebSearch", "WebFetch"]

# Bounded to keep SSE event payload within ~8KB upstream framing limits
# (Cloudflare, nginx default proxy_buffer_size); leaves headroom for the
# rest of ErrorEvent's serialized form.  This is a wire-shape invariant
# tied to the upstream proxy framing limits — NOT a deployment-environment
# tunable, so it lives next to the slice site rather than in AppConfig.
STDERR_TAIL_BYTES: int = 4096
