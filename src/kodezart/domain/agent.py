"""Pure domain logic for agent operations — no I/O, no side effects."""

import hashlib
import json
import uuid

from pydantic import ValidationError

from kodezart.domain.errors import LaneEntryError
from kodezart.types.domain.agent import RulingId
from kodezart.types.domain.branch import LaneBranchName


def mint_ruling_id(*, issue_ref: str, question: str) -> RulingId:
    """Name an exact issue/question pair, independently of its answer or run.

    JSON framing preserves component boundaries; no normalization folds
    distinct issue keys or reworded questions into an existing ruling.
    """
    if not issue_ref.strip() or not question.strip():
        raise ValueError("a ruling requires a nonempty issue reference and question")
    framed = json.dumps(
        (issue_ref, question), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return RulingId(hashlib.sha256(framed).hexdigest())


def generate_workspace_id() -> str:
    """Generate a unique 32-character hex identifier for workspace isolation."""
    return uuid.uuid4().hex


def generate_ralph_branch_name(feature_branch: str) -> str:
    """Append ``-ralph-{8-char-hex}`` to *feature_branch*."""
    short_hash = uuid.uuid4().hex[:8]
    return f"{feature_branch}-ralph-{short_hash}"


def mint_lane_branches(issue_key: str) -> tuple[str, str]:
    """The (deliverable, loop) names a lane nothing is recorded for starts on.

    Arithmetic over the issue key and one drawn short id, and the only place
    the tracker-native arm draws one: naming a lane's branch needs no
    judgement, so it needs no session either, and a lane re-entered from its
    record never reaches here at all.

    The name is NOT passed through the outbound content gate. The gate sends
    every branch-name write to the judgement scanner, which exists for a
    model's summary of raw task text; this name carries an issue key and hex
    and nothing authored. On a public repository it therefore shows the key.
    """
    try:
        deliverable = str(
            LaneBranchName(issue_key=issue_key, short_id=uuid.uuid4().hex[:8])
        )
    except ValidationError as exc:
        raise LaneEntryError(
            issue_key=issue_key,
            reason="the issue key cannot stand inside a branch ref",
        ) from exc
    return deliverable, generate_ralph_branch_name(deliverable)


def best_iteration_ref(feature_branch: str) -> str:
    """Append ``-best`` to *feature_branch*.

    Deterministic, unlike the ralph branch name: the ref names the run
    that produced it rather than an occasion, so publishing twice within
    a run targets one ref instead of littering the remote.  The feature
    branch already carries a per-run suffix, so two runs never collide.
    """
    return f"{feature_branch}-best"
