"""LangGraph checkpoint reader — the only place ``aget_tuple`` appears.

Every LangGraph detail lives behind this seam: the thread id derivation,
the ``aget_tuple`` call, and the unpacking of ``channel_values`` and
``versions_seen`` into a typed ``RunState``.
"""

from collections.abc import Mapping

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from kodezart.types.domain.run import RunState

# LangGraph's bookkeeping pseudo-nodes; neither is a graph node a reader
# should report as the last one that completed.
_RESERVED_NODES: frozenset[str] = frozenset({"__input__", "__start__"})

# Channels of ``WorkflowState`` this reader surfaces.  Absent channels
# fall back to no value at all — ``RunState`` carries the field defaults,
# which are ``WorkflowState``'s own initial values.
_RUN_STATE_CHANNELS: tuple[str, ...] = (
    "total_iterations",
    "fix_rounds_used",
    "accepted",
    "merged",
    "review_passed",
    "ci_passed",
    "ci_summary",
    "pr_url",
    "pr_number",
    "feature_branch",
    "ralph_branch",
)


def _last_completed_node(
    versions_seen: Mapping[str, Mapping[str, object]],
) -> str | None:
    """The node holding the highest channel version it has consumed.

    LangGraph versions are zero-padded, lexicographically ordered
    strings, so the maximum identifies the most recent node regardless
    of the order nodes were first inserted.
    """
    best_node: str | None = None
    best_version: str = ""
    for node, channels in versions_seen.items():
        if node in _RESERVED_NODES or not channels:
            continue
        version = max(str(value) for value in channels.values())
        if version >= best_version:
            best_node = node
            best_version = version
    return best_node


class LangGraphRunStateReader:
    """Reads a run's checkpointed state. Satisfies ``RunStateReader``."""

    def __init__(self, *, checkpointer: BaseCheckpointSaver[str]) -> None:
        self._checkpointer: BaseCheckpointSaver[str] = checkpointer

    async def read(self, *, job_id: str) -> RunState | None:
        """Checkpointed state for *job_id*, or ``None`` when none exists.

        The job id IS the outer graph's thread id.
        """
        config: RunnableConfig = {"configurable": {"thread_id": job_id}}
        snapshot = await self._checkpointer.aget_tuple(config)
        if snapshot is None:
            return None
        values: Mapping[str, object] = snapshot.checkpoint["channel_values"]
        payload: dict[str, object] = {
            channel: values[channel]
            for channel in _RUN_STATE_CHANNELS
            if channel in values
        }
        payload["last_completed_node"] = _last_completed_node(
            snapshot.checkpoint["versions_seen"],
        )
        return RunState.model_validate(payload)
