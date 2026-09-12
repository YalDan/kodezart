"""External workspace and structured result fixtures for the shared judge."""

from kodezart.types.domain.agent import ResultEvent
from tests.fakes import FakeWorkspaceProvider


def result(**changes: object) -> ResultEvent:
    return ResultEvent.model_validate(
        {
            "result": "Author transcript is not fresh evidence.",
            "session_id": "previous-agent-session",
            "subtype": "result",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": 1,
            **changes,
        }
    )


class RecordingWorkspace(FakeWorkspaceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.arguments: list[dict[str, object]] = []

    async def acquire(self, **kwargs):
        self.arguments.append(kwargs)
        return await super().acquire(**kwargs)
