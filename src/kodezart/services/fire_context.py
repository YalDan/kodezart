"""Assembling a fire's context: the ticket, plus every asset it references.

Two decisions the issue left open, taken here and recorded on it:

* **Which assets are required** — all of them.  A discriminator would need a
  marker only the ticket's author can set, and an unmarked-but-needed asset
  would then be skipped silently, which is the exact failure deliverable 4
  exists to remove.  Fetching every reference and failing loudly on any one
  is the behaviour with no silent arm.
* **Where fetched content lives** — in the fire's context value, reproduced
  verbatim in the text the session receives.  Nothing is written to a
  workspace at dispatch time because no workspace exists yet: the fire is
  enqueued, not run, at this point.

The three bounds are ``AppConfig`` fields and are enforced here rather than
in the adapter, so every backend is bounded identically.

**Tracker-resident content is untrusted on the way IN** (KOD-107 R1).  Every
fetched document passes KOD-47's gate before it becomes part of a context,
and only a CLEAN verdict enters.  The alternative the issue names — trusting
a document's own claim to be sanitized — is the defect it reports: the two
documents that produced it are titled "sanitized" and are not, so a
machine-readable marker would be the same false claim in stricter syntax.
Nothing here consults a title, a label or a marker; the body is the only
evidence.
"""

import asyncio

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.domain.errors import AssetFetchError
from kodezart.types.domain.fire import FireAsset, FireContext
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)

#: The posture every fetched document is judged at.  Not a guess about where
#: the content will go: it is the surface KOD-47's recorded incident actually
#: reached — a land-stage agent writing tracker links into a pull-request
#: body on a public repository.  Content that could not be written there does
#: not enter a context a session composes that field from, and a private
#: repository buys no relaxation, because visibility is resolved per run and
#: a fire outlives the assumption.
_INBOUND_VISIBILITY = RepoVisibility.PUBLIC
_INBOUND_SHAPE = WriterShape.PROSE
_INBOUND_DESTINATION = OutboundDestination.PR_BODY

#: A tracker document is written by whoever wrote it, and this process was
#: not there.  Its provenance is unknown, which is not ``DERIVED``: nothing
#: here can recompute the body from durable state, and the recompute test
#: answers "no" for anything it cannot answer "yes" for.  The audited bucket
#: is the safe one, and this is exactly the content KOD-107 reports.
_INBOUND_CONTENT_CLASS = ContentClass.AUTHORED


class FireContextAssembler:
    """Builds the ``FireContext`` a dispatched fire is enqueued with."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        gate: OutboundContentGate,
        max_count: int,
        max_bytes: int,
        fetch_timeout_seconds: float,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._gate: OutboundContentGate = gate
        self._max_count: int = max_count
        self._max_bytes: int = max_bytes
        self._fetch_timeout_seconds: float = fetch_timeout_seconds
        self._log: BoundLogger = get_logger(__name__)

    async def assemble(self, *, issue_key: str, body: str) -> FireContext:
        """The context for *issue_key*, or a typed error and no context."""
        referenced = await self._tracker.list_issue_assets(issue_key=issue_key)
        if len(referenced) > self._max_count:
            raise AssetFetchError(
                f"ticket references {len(referenced)} assets, "
                f"more than the configured maximum of {self._max_count}",
                issue_key=issue_key,
                reason="too_many",
            )
        assets = [
            await self._fetch(
                issue_key=issue_key, asset_key=asset.asset_key, title=asset.title
            )
            for asset in referenced
        ]
        await self._log.ainfo(
            "fire_context_assembled",
            issue_key=issue_key,
            asset_keys=[asset.asset_key for asset in assets],
        )
        return FireContext(issue_key=issue_key, body=body, assets=tuple(assets))

    async def _fetch(self, *, issue_key: str, asset_key: str, title: str) -> FireAsset:
        try:
            async with asyncio.timeout(self._fetch_timeout_seconds):
                content = await self._tracker.read_document(document_key=asset_key)
        except TimeoutError as exc:
            raise AssetFetchError(
                "asset fetch exceeded the configured timeout",
                issue_key=issue_key,
                reason="timeout",
                asset_key=asset_key,
            ) from exc
        except Exception as exc:
            raise AssetFetchError(
                "asset could not be read through the tracker port",
                issue_key=issue_key,
                reason="unreadable",
                asset_key=asset_key,
            ) from exc

        asset = FireAsset(asset_key=asset_key, title=title, content=content)
        if asset.size_bytes() > self._max_bytes:
            raise AssetFetchError(
                f"asset is {asset.size_bytes()} bytes, "
                f"more than the configured maximum of {self._max_bytes}",
                issue_key=issue_key,
                reason="too_large",
                asset_key=asset_key,
            )
        await self._admit(issue_key=issue_key, asset=asset)
        return asset

    async def _admit(self, *, issue_key: str, asset: FireAsset) -> None:
        """Refuse *asset* unless the gate returns CLEAN. No third arm.

        REDACTED refuses as hard as BLOCKED.  Admitting a redacted document
        would hand the session a silently altered input to build from, and a
        fire built on a doctored asset is worse than one that did not start.
        A scanner with no answer needs no rule of its own: KOD-47 resolves
        every ``ScanFailureKind`` to BLOCKED, so "did not answer" reaches
        this refusal by construction rather than by a second condition.
        """
        decision = await self._gate.gate(
            content=asset.content,
            visibility=_INBOUND_VISIBILITY,
            shape=_INBOUND_SHAPE,
            destination=_INBOUND_DESTINATION,
            content_class=_INBOUND_CONTENT_CLASS,
        )
        await self._log.ainfo(
            "fire_asset_gated",
            issue_key=issue_key,
            asset_key=asset.asset_key,
            verdict=decision.verdict.value,
            categories=[category.value for category in decision.categories],
            failure=None if decision.failure is None else decision.failure.value,
        )
        if decision.verdict is GateVerdict.CLEAN:
            return
        raise AssetFetchError(
            "asset carries content that may not enter a fire context "
            f"(verdict {decision.verdict.value})",
            issue_key=issue_key,
            reason="private_content",
            asset_key=asset.asset_key,
        )
