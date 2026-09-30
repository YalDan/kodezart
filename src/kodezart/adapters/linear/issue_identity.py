"""An issue identity stored in the same description write that creates it."""

import json
from collections.abc import Mapping

from pydantic import ValidationError

from kodezart.core.errors import TrackerProtocolError
from kodezart.domain.comment_markers import configured_marker_prefix
from kodezart.types.domain.issue_identity import IssueIdentity


class LinearIssueIdentityCarrier:
    """The first description line is an operation-owned HTML metadata comment.

    The ordinary body remains the tracker's raw description. Consumers
    read the decoded identity through the port and never parse this line.
    """

    def __init__(self, prefixes: Mapping[str, str]) -> None:
        self._prefixes = prefixes

    def require_prefix(self) -> str:
        return configured_marker_prefix(self._prefixes, purpose="issue_identity")

    def decode(self, body: str, *, issue_key: str) -> IssueIdentity | None:
        # An undeclared prefix identifies no carrier this operation owns.
        # Explicit identity reads and writes require it before calling here.
        prefix = self._prefixes.get("issue_identity")
        if prefix is None:
            return None
        start = f"<!-- {prefix} "
        first = body.splitlines()[0] if body else ""
        if not first.startswith(start):
            return None
        suffix = " -->"
        if not first.endswith(suffix):
            raise TrackerProtocolError(
                "issue identity carrier is incomplete",
                tool="get_issue",
                detail=f"issue={issue_key}",
            )
        try:
            return IssueIdentity.model_validate_json(first[len(start) : -len(suffix)])
        except ValidationError as exc:
            raise TrackerProtocolError(
                "issue identity carrier is malformed",
                tool="get_issue",
                detail=f"issue={issue_key}",
            ) from exc

    def encode(self, identity: IssueIdentity, *, body: str, issue_key: str) -> str:
        prefix = self.require_prefix()
        held = self.decode(body, issue_key=issue_key)
        if held is not None:
            if held != identity:
                raise TrackerProtocolError(
                    "a description edit cannot replace an issue's identity",
                    tool="save_issue",
                    detail=f"issue={issue_key}",
                )
            return body
        payload = json.dumps(identity.model_dump(by_alias=True), separators=(",", ":"))
        # An opaque identity can contain HTML delimiters or Unicode line
        # separators; its encoding must still occupy one metadata line.
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
        return f"<!-- {prefix} {payload} -->\n\n{body}"
