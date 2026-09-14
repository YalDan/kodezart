"""Observe the native opening frames at the harness's actual dispatch seam."""

from collections.abc import Callable

from kodezart.types.domain.agent import (
    AgentEvent,
    NodeSessionStartedEvent,
    ResultEvent,
    SystemEvent,
)
from kodezart.types.domain.node_session import (
    NodeInvocation,
    NodeSessionObservationError,
)


class NodeSessionObserver:
    """One observation window, with repeated opening frames deduplicated.

    Malformed native evidence is refused after the stream has drained, so a
    malformed frame cannot abandon a still-owned executor or workspace.
    No opening is inferred from a request, result, log, or model response.
    """

    def __init__(
        self,
        *,
        invocation: NodeInvocation,
        emit: Callable[[NodeSessionStartedEvent], None],
    ) -> None:
        self._invocation = invocation
        self._emit = emit
        self._sessions: set[str] = set()
        self._failures: list[str] = []

    def observe(self, event: AgentEvent) -> None:
        if isinstance(event, SystemEvent) and event.subtype == "init":
            session_id = event.data.get("session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                self._failures.append("native opening does not report a session id")
                return
            if session_id in self._sessions:
                return
            self._sessions.add(session_id)
            self._emit(
                NodeSessionStartedEvent(
                    invocation=self._invocation, session_id=session_id
                )
            )
        elif isinstance(event, ResultEvent) and event.session_id not in self._sessions:
            self._failures.append("native result has no observed session opening")

    def require_valid(self) -> None:
        if self._failures:
            raise NodeSessionObservationError(
                invocation=self._invocation, failures=tuple(self._failures)
            )
