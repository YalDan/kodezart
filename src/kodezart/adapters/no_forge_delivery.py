"""The delivery probe for an origin with no forge behind it.

A peer of the forge adapter on ``DeliveryProbe``, not a degraded mode of
it: the forge client keeps raising loudly on URLs it does not own, and
which probe answers for a repository is decided once, at the composition
root, from that repository's origin (KOD-145).
"""

from kodezart.core.logging import BoundLogger, get_logger


class NoForgeDeliveryProbe:
    """``DeliveryProbe`` for an origin that has no forge to be asked.

    Where pull requests cannot exist, no open pull request delivers
    anything — so the answer is False, and it is a TRUE statement about
    the origin rather than a fallback standing in for an answer nobody
    could get.  The fire path already models the same fact at its other
    end, as the ``review_passed_no_pr_adapter`` terminal outcome.

    The distinction matters for what the caller does next: the dispatcher
    reads False as "nothing already delivers this issue, so it is
    eligible", which is exactly right for a local bare origin — no pull
    request is in flight because none can be.  A probe that raised
    instead, as the forge client does on a URL it cannot parse, took the
    whole dispatch tick down every interval on the first live run.
    """

    def __init__(self) -> None:
        self._log: BoundLogger = get_logger(__name__)

    async def open_delivery_exists(
        self,
        *,
        repo_url: str,
        issue_key: str,
    ) -> bool:
        """False — this origin has no pull requests for one to be found in."""
        await self._log.adebug(
            "no_forge_delivery_probe_answered",
            repo_url=repo_url,
            issue_key=issue_key,
        )
        return False
