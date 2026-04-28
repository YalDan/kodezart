"""Pure domain logic for agent operations — no I/O, no side effects."""

import uuid


def generate_job_id() -> str:
    """Generate a unique 32-character hex job identifier for workspace isolation."""
    return uuid.uuid4().hex


def generate_ralph_branch_name(feature_branch: str) -> str:
    """Append ``-ralph-{8-char-hex}`` to *feature_branch*."""
    short_hash = uuid.uuid4().hex[:8]
    return f"{feature_branch}-ralph-{short_hash}"
