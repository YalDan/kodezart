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
"""

import asyncio

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import AssetFetchError
from kodezart.types.domain.fire import FireAsset, FireContext


class FireContextAssembler:
    """Builds the ``FireContext`` a dispatched fire is enqueued with."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        max_count: int,
        max_bytes: int,
        fetch_timeout_seconds: float,
    ) -> None:
        self._tracker: TrackerPort = tracker
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
        return asset
