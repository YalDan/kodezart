"""The delivery probe for an origin that has no forge behind it.

A peer implementation of ``DeliveryProbe``, not a degraded forge client:
the answer it gives is TRUE about the origin rather than a stand-in for
one it could not obtain (KOD-145).
"""

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.core.protocols import DeliveryProbe

LOCAL_ORIGIN = "file:///tmp/fixture-origin.git"


def test_the_probe_satisfies_the_whole_port() -> None:
    """Substitutable for the forge client wherever a probe is wired."""
    assert isinstance(NoForgeDeliveryProbe(), DeliveryProbe)


async def test_no_open_delivery_exists_where_pull_requests_cannot() -> None:
    """False by design: no open pull request delivers this, because none can.

    The same fact the fire path already models at its other end, as the
    ``review_passed_no_pr_adapter`` terminal outcome.  The dispatcher reads
    it as "nothing already delivers this issue", which is exactly right for
    a local bare origin.
    """
    assert not await NoForgeDeliveryProbe().open_delivery_exists(
        repo_url=LOCAL_ORIGIN,
        issue_key="K-1",
    )


async def test_the_answer_does_not_depend_on_the_issue_it_is_asked_about() -> None:
    """Not a lookup that happens to miss: a statement about the origin."""
    probe = NoForgeDeliveryProbe()

    for issue_key in ("K-1", "K-2", "KOD-58"):
        assert not await probe.open_delivery_exists(
            repo_url=LOCAL_ORIGIN,
            issue_key=issue_key,
        )
