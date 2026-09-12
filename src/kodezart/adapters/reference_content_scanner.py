"""Classify URL tokens using native parsing and deployment privacy facts."""

import re
from html import unescape

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from kodezart.adapters.linear_references import LINEAR_WEB_HOSTS, linear_reference
from kodezart.core.errors import ContentScannerBootError
from kodezart.types.domain.gating import (
    OutboundDestination,
    OutboundSurface,
    RedactionCategory,
    ScanFailureKind,
    ScanHit,
    ScanResult,
    surface_of,
)
from kodezart.types.domain.privacy import PrivateSurface, WebReference

_HTTP_URL = TypeAdapter(AnyHttpUrl)
# Token boundaries only. No vendor, workspace, key or privacy grammar.
_URL_TOKENS = re.compile(r"(?:https?:)?//[^\s<>()\"'`]+", re.IGNORECASE)
# CommonMark character references and punctuation escapes, decoded once.
_TEXT_ESCAPES = re.compile(
    r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~\\])"
    r"|&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);"
)


def _reference_text(content: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize displayed references while retaining each character's raw span."""
    chunks: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for escape in _TEXT_ESCAPES.finditer(content):
        chunks.append(content[cursor : escape.start()])
        spans.extend((index, index + 1) for index in range(cursor, escape.start()))
        decoded = escape.group(1) or unescape(escape.group())
        chunks.append(decoded)
        spans.extend((escape.start(), escape.end()) for _ in decoded)
        cursor = escape.end()
    chunks.append(content[cursor:])
    spans.extend((index, index + 1) for index in range(cursor, len(content)))
    return "".join(chunks), spans


def private_reference_category(
    reference: WebReference,
    *,
    private_surface: PrivateSurface,
    destination: OutboundDestination,
) -> RedactionCategory | None:
    """Classify parsed facts; semantic judgment still covers authored text."""
    if reference.host in private_surface.hosts:
        return RedactionCategory.INFRA_ENDPOINTS
    if (
        surface_of(destination) is not OutboundSurface.TRACKER
        and reference.workspace is not None
        and reference.workspace in private_surface.workspaces.get(reference.host, ())
    ):
        return RedactionCategory.TRACKER_URLS
    return None


class ReferenceContentScanner:
    """The existing gate's deterministic native-reference participant."""

    def __init__(self, *, private_surface: PrivateSurface) -> None:
        unsupported = private_surface.workspaces.keys() - LINEAR_WEB_HOSTS
        if unsupported:
            raise ContentScannerBootError(
                "No native workspace URL parser exists for a configured private host",
                missing="private_surface.workspaces native parser",
            )
        self._private_surface = private_surface.model_copy(deep=True)

    async def scan(
        self, *, content: str, destination: OutboundDestination
    ) -> ScanResult:
        hits: list[ScanHit] = []
        reference_text, spans = _reference_text(content)
        for token in _URL_TOKENS.finditer(reference_text):
            normalized = token.group().rstrip(".,;:!?")
            try:
                url = _HTTP_URL.validate_python(
                    "https:" + normalized if normalized.startswith("//") else normalized
                )
                reference = linear_reference(url)
            except (ValidationError, ValueError):
                return ScanResult(failure=ScanFailureKind.MALFORMED_VERDICT)
            category = private_reference_category(
                reference,
                private_surface=self._private_surface,
                destination=destination,
            )
            if category is not None:
                hits.append(
                    ScanHit(
                        category=category,
                        start=spans[token.start()][0],
                        end=spans[token.start() + len(normalized) - 1][1],
                        rationale="Reference matches a private deployment fact",
                    )
                )
        return ScanResult(hits=tuple(hits))
