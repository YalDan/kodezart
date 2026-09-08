"""Classify URL tokens using native parsing and deployment privacy facts."""

import re

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from kodezart.adapters.linear_references import LINEAR_WEB_HOSTS, linear_reference
from kodezart.core.errors import ContentScannerBootError
from kodezart.types.domain.gating import (
    UNCONDITIONAL_ROUTING,
    OutboundDestination,
    OutboundSurface,
    RedactionCategory,
    ScanFailureKind,
    ScanHit,
    ScannerRouting,
    ScanResult,
    surface_of,
)
from kodezart.types.domain.privacy import PrivateSurface, WebReference

_HTTP_URL = TypeAdapter(AnyHttpUrl)
# Token boundaries only. No vendor, workspace, key or privacy grammar.
_URL_TOKENS = re.compile(r"https?://[^\s<>()\"'`]+", re.IGNORECASE)


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

    @property
    def routing(self) -> ScannerRouting:
        return UNCONDITIONAL_ROUTING

    async def scan(
        self, *, content: str, destination: OutboundDestination
    ) -> ScanResult:
        hits: list[ScanHit] = []
        for token in _URL_TOKENS.finditer(content):
            raw = token.group().rstrip(".,;:!?")
            try:
                url = _HTTP_URL.validate_python(raw)
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
                        start=token.start(),
                        end=token.start() + len(raw),
                        rationale="Reference matches a private deployment fact",
                    )
                )
        return ScanResult(hits=tuple(hits))
