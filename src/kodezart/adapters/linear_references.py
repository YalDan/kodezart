"""Linear's workspace-bearing web addresses, parsed without a network call."""

from urllib.parse import unquote

from pydantic import AnyHttpUrl

from kodezart.types.domain.privacy import WebReference

LINEAR_WEB_HOSTS = frozenset({"linear.app", "www.linear.app"})


def linear_reference(url: AnyHttpUrl) -> WebReference:
    """Retain the addressed host and decoded native workspace segment.

    Callers supply a validated HTTP URL. The host is preserved rather than
    replacing it with a vendor default; deployment facts name that host.
    """
    host = (url.host or "").rstrip(".").casefold()
    segments = (url.path or "/").split("/")
    workspace = (
        unquote(segments[1], errors="strict").casefold()
        if host in LINEAR_WEB_HOSTS and len(segments) > 1 and segments[1]
        else None
    )
    if workspace is not None and any(character in workspace for character in "/\\"):
        raise ValueError("Encoded workspace path separators are ambiguous")
    return WebReference(host=host, workspace=workspace)
