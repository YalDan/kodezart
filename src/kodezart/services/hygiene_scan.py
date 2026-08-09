"""The pre-promotion hygiene scan over a frozen fire body.

Written against ``ContentScanner`` — the port the sanitization set already
runs through — and it holds nothing else.  There is no second scanning
engine here and there is no regular expression here: this module imports
neither ``re`` nor any adapter, so the quality-vocabulary set reaches a
body through exactly the entry point the deny set does.  A test reads the
module's imports and fails if that ever stops being true.

The question the scan asks is not the gate's question.  The gate asks
whether a payload may leave the process; this asks whether the implementer
who receives the body can act on it alone.  Same arithmetic, different
pattern set, different verdict — which is why the answer is a
:class:`HygieneReport` and not a :class:`GateDecision`.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import ContentScanner
from kodezart.types.domain.gating import (
    HygieneReport,
    OutboundDestination,
    ScanCategory,
)


class HygieneScan:
    """Runs one configured quality pattern set over one candidate body."""

    def __init__(self, *, scanner: ContentScanner) -> None:
        self._scanner: ContentScanner = scanner
        self._log: BoundLogger = get_logger(__name__)

    async def inspect(
        self,
        *,
        body: str,
        destination: OutboundDestination,
    ) -> HygieneReport:
        """Whether *body* may be promoted, and every category that stopped it.

        *destination* is the surface the body is bound for.  The
        deterministic engine's answer does not depend on it, but the port
        requires it and a scan whose destination was invented at the call
        site would be lying to any scanner that DID depend on it.
        """
        result = await self._scanner.scan(content=body, destination=destination)
        if result.failure is not None:
            await self._log.awarning(
                "fire_body_hygiene_unanswered",
                destination=destination.value,
                failure=result.failure.value,
            )
            return HygieneReport(promotable=False, failure=result.failure)

        categories: list[ScanCategory] = []
        for hit in result.hits:
            if hit.category not in categories:
                categories.append(hit.category)
        report = HygieneReport(
            promotable=not result.hits,
            categories=tuple(categories),
            hits=result.hits,
        )
        await self._log.ainfo(
            "fire_body_hygiene_scanned",
            destination=destination.value,
            promotable=report.promotable,
            categories=[category.value for category in report.categories],
            hit_count=len(report.hits),
        )
        return report
