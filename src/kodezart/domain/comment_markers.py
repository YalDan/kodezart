"""Comment identities use operation-owned prefixes."""

from collections.abc import Mapping
from urllib.parse import quote

from kodezart.types.domain.operation import OperationMemberAbsentError


def configured_marker_prefix(prefixes: Mapping[str, str], *, purpose: str) -> str:
    """Refuse an absent purpose where it is needed, never guess its prefix."""
    if purpose not in prefixes:
        raise OperationMemberAbsentError(
            missing=f"marker_prefixes[{purpose!r}]",
            stops="this tracker comment identity cannot be read or written",
        )
    return prefixes[purpose]


def compose_comment_marker(
    *,
    prefixes: Mapping[str, str],
    purpose: str,
    lane: str,
    occurrence_key: str | None = None,
) -> str:
    """Compose one lane marker, with an occurrence key for recurring purposes.

    Percent-encoding keeps a delimiter inside one identity component from
    colliding with a delimiter between components.
    """
    prefix = configured_marker_prefix(prefixes, purpose=purpose)
    if not lane or occurrence_key == "":
        raise ValueError("marker identity components must be nonempty")
    parts = [prefix, quote(lane, safe="")]
    if occurrence_key is not None:
        parts.append(quote(occurrence_key, safe=""))
    return f"[{':'.join(parts)}]"
