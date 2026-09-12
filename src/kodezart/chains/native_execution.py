"""Checkpoint the real native writer, reconciliation and persistence phases."""

from collections.abc import Awaitable
from typing import Literal, Protocol, assert_never

from langgraph.graph import END, START, StateGraph
from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.native_execution import (
    NativeExecutionPhase,
    NewNativeExecution,
    PersistedNativeExecution,
    PreparedNativeExecution,
    ReconciledNativeExecution,
    RefusedNativeExecution,
    UnchangedNativeExecution,
    WrittenNativeExecution,
)


class NativeExecutionState(CamelCaseModel):
    """Only completed, validated phase data enters the existing checkpointer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    execution: NativeExecutionPhase


class NativeExecutionActions(Protocol):
    """Existing native effects bound to current runtime ports, never serialized."""

    async def prepare(self) -> PreparedNativeExecution: ...

    async def write(self, phase: PreparedNativeExecution) -> WrittenNativeExecution: ...

    async def reconcile(
        self, phase: WrittenNativeExecution
    ) -> ReconciledNativeExecution | RefusedNativeExecution: ...

    async def persist(
        self, phase: ReconciledNativeExecution
    ) -> PersistedNativeExecution | UnchangedNativeExecution: ...


class NativeExecutionGraph:
    """One consumed phase graph over the existing native service operations."""

    def __init__(self, *, actions: NativeExecutionActions) -> None:
        self._actions = actions
        self.failure: BaseException | None = None
        graph: StateGraph[
            NativeExecutionState, None, NativeExecutionState, NativeExecutionState
        ] = StateGraph(NativeExecutionState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("write", self._write)
        graph.add_node("reconcile", self._reconcile)
        graph.add_node("persist", self._persist)
        destinations = ["prepare", "write", "reconcile", "persist", END]
        graph.add_conditional_edges(START, self._next, destinations)
        for node in ("prepare", "write", "reconcile", "persist"):
            graph.add_conditional_edges(node, self._next, destinations)
        self.graph = graph.compile()

    async def _invoke[ResultT](self, action: Awaitable[ResultT]) -> ResultT:
        try:
            return await action
        except BaseException as failure:
            # The installed runner can suppress a child task's cancellation.
            # Retain the actual exception for the owning stream, never state.
            self.failure = failure
            raise

    @staticmethod
    def _next(
        state: NativeExecutionState,
    ) -> Literal["prepare", "write", "reconcile", "persist", "__end__"]:
        phase = state.execution
        if isinstance(phase, NewNativeExecution):
            return "prepare"
        if isinstance(phase, PreparedNativeExecution):
            return "write"
        if isinstance(phase, WrittenNativeExecution):
            return "reconcile"
        if isinstance(phase, ReconciledNativeExecution):
            return "persist"
        if isinstance(
            phase,
            (
                PersistedNativeExecution,
                UnchangedNativeExecution,
                RefusedNativeExecution,
            ),
        ):
            return "__end__"
        assert_never(phase)

    async def _prepare(self, state: NativeExecutionState) -> dict[str, object]:
        if not isinstance(state.execution, NewNativeExecution):
            raise ValueError("workspace preparation requires a new native execution")
        return {"execution": await self._invoke(self._actions.prepare())}

    async def _write(self, state: NativeExecutionState) -> dict[str, object]:
        if not isinstance(state.execution, PreparedNativeExecution):
            raise ValueError("native writing requires a prepared workspace")
        return {"execution": await self._invoke(self._actions.write(state.execution))}

    async def _reconcile(self, state: NativeExecutionState) -> dict[str, object]:
        if not isinstance(state.execution, WrittenNativeExecution):
            raise ValueError("reconciliation requires an actual completed writer")
        return {
            "execution": await self._invoke(self._actions.reconcile(state.execution))
        }

    async def _persist(self, state: NativeExecutionState) -> dict[str, object]:
        if not isinstance(state.execution, ReconciledNativeExecution):
            raise ValueError("persistence requires actual reconciliation receipts")
        return {"execution": await self._invoke(self._actions.persist(state.execution))}
