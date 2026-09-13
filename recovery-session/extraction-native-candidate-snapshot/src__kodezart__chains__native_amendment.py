"""The actual native claim-to-canonical-verdict graph before persistence."""

from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentVerdict,
    NativeWriterOutput,
)


class AmendmentActions(Protocol):
    """Runtime collaborators bound to the current writer; never checkpoint data."""

    async def judge_claim(self, claim: AmendmentClaim) -> AmendmentJudgment:
        """Independently judge one current claim at the immutable base."""
        ...

    async def apply_judgment(
        self, claim: AmendmentClaim, judgment: AmendmentJudgment
    ) -> AmendmentVerdict:
        """Complete the authorized writes through canonical bounded verification."""
        ...

    async def require_current(self) -> None:
        """Recheck the real source after awaited judgments and writes."""
        ...


class AmendmentState(TypedDict):
    """Only actual claims, judgments and completed verdicts travel through nodes."""

    output: NativeWriterOutput
    judgments: tuple[AmendmentJudgment, ...]
    verdicts: tuple[AmendmentVerdict, ...]


class NativeAmendmentGraph:
    """One compiled, consumed precommit graph; no tracker or agent in its state."""

    def __init__(self, *, actions: AmendmentActions) -> None:
        self._actions = actions
        graph = StateGraph(AmendmentState)
        graph.add_node("judge", self._judge)
        graph.add_node("canonical_write_back", self._write_back)
        graph.add_node("complete", self._complete)
        graph.add_conditional_edges(
            START, self._next, {"judge": "judge", "complete": "complete"}
        )
        graph.add_edge("judge", "canonical_write_back")
        graph.add_conditional_edges(
            "canonical_write_back",
            self._next,
            {"judge": "judge", "complete": "complete"},
        )
        graph.add_edge("complete", END)
        self.graph = graph.compile()

    async def _judge(self, state: AmendmentState) -> dict[str, object]:
        claim = state["output"].claims[len(state["verdicts"])]
        await self._actions.require_current()
        judgment = await self._actions.judge_claim(claim)
        await self._actions.require_current()
        return {"judgments": (*state["judgments"], judgment)}

    async def _write_back(self, state: AmendmentState) -> dict[str, object]:
        claim = state["output"].claims[len(state["verdicts"])]
        await self._actions.require_current()
        verdict = await self._actions.apply_judgment(claim, state["judgments"][-1])
        return {"verdicts": (*state["verdicts"], verdict)}

    def _next(self, state: AmendmentState) -> Literal["judge", "complete"]:
        return (
            "judge"
            if len(state["verdicts"]) < len(state["output"].claims)
            else "complete"
        )

    async def _complete(self, state: AmendmentState) -> dict[str, object]:
        await self._actions.require_current()
        if len(state["verdicts"]) != len(state["output"].claims):
            raise ValueError("every claimed departure requires a completed verdict")
        return {}

    async def run(self, *, output: NativeWriterOutput) -> AmendmentReport:
        """Return only after every actual native claim has reached the final node."""
        initial: AmendmentState = {"output": output, "judgments": (), "verdicts": ()}
        final = await self.graph.ainvoke(initial)
        return AmendmentReport(verdicts=final["verdicts"])
