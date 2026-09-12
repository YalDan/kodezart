"""Pure domain logic for agent operations — no I/O, no side effects."""

import hashlib
import json
import uuid

from kodezart.types.domain.agent import RulingId


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


def best_iteration_ref(feature_branch: str) -> str:
    """Append ``-best`` to *feature_branch*.

    Deterministic, unlike the ralph branch name: the ref names the run
    that produced it rather than an occasion, so publishing twice within
    a run targets one ref instead of littering the remote.  The feature
    branch already carries a per-run suffix, so two runs never collide.
    """
    return f"{feature_branch}-best"
