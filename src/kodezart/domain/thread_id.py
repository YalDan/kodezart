"""Thread-id derivation — the one place a job id becomes a thread id.

A job id addresses the outer graph's checkpoints directly; the two
sub-graphs get their own threads, derived from that same id by suffix.
Both sides of the checkpoint call these functions: the chains derive
their thread id to WRITE checkpoints as they execute, and the run-state
reader derives it to READ them.  Keeping the rule here means the suffixes
exist once rather than being re-interpolated per module — and it keeps a
chain from having to reach into an adapter to learn its own thread id.

Pure: no I/O, no graph library, no configuration.
"""

_RALPH_SUFFIX: str = "-ralph"
_TICKET_SUFFIX: str = "-ticket"


def workflow_thread_id(job_id: str) -> str:
    """Thread id of the outer workflow graph — the job id itself."""
    return job_id


def ralph_thread_id(job_id: str) -> str:
    """Thread id of the quality-gate sub-graph for *job_id*."""
    return f"{job_id}{_RALPH_SUFFIX}"


def ticket_thread_id(job_id: str) -> str:
    """Thread id of the ticket-generation sub-graph for *job_id*."""
    return f"{job_id}{_TICKET_SUFFIX}"
