"""Ownership arbitration, measured against the real tracker.

The fake MCP server proves the adapter obeys its own reading of the
backend.  It proves nothing about the backend.  What this module measures
is the half the arithmetic rests on and no double can establish: that
creation order is server-assigned and stable across reads, that a listing
after a write contains that write, that an edit keeps a comment's place,
and that deletion takes it off.  Then it runs the races themselves —
two claimants at once, a renewal after an expiry the issue changed hands
across, an intersecting lease and a disjoint one — over two adapter
instances on two sessions, exactly as two deployments would.

Everything is written to ONE issue, found by title or created once, and
every marker the probe mints is deleted before the session ends.

Live only (``pytest -m live``): it dials the operator's tracker.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import (
    build_tracker,
    make_mcp_tool_caller,
    refuse_foreign_credential,
)
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.core.errors import McpTransportError
from kodezart.core.protocols import (
    ManagedMcpToolCaller,
    McpToolCaller,
    McpToolResult,
    TrackerPort,
)
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import ClaimStatus, IssuePriority, IssueQuery
from tests.probes.recording import record

#: One board, one pair of sessions, one probe issue for the whole module —
#: so the cases hand ownership to each other the way two deployments would.
#: The sessions live in the fixture's loop, and the cases have to run in
#: that same loop to await them at all.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

PROBE = "live-ownership"

#: How the probe addresses its own plain scratch comments, so a run that
#: died before deleting one can be swept by the run after it. Every one
#: the probe writes names the probe and the run that wrote it.
PROBE_COMMENT = re.compile(r"^probe [0-9a-f]{8} ")

#: The one issue this module is allowed to write to. Found by this exact
#: title, created once if the board does not carry it yet.
PROBE_TITLE = "kodezart live probe — ownership arbitration"

PROBE_BODY = (
    "Scratch issue for the ownership-arbitration probe. Every comment on it "
    "is written and deleted by a probe run; nothing here is a record."
)

#: The marker identity the probe writes its grants under. The deployment's
#: own operation config declares none, and a probe-owned prefix also keeps
#: every marker it mints unmistakably the probe's.
PROBE_MARKER_PREFIX = "kodezart-probe-claim"

#: The team the operation calls its own board.
PROBE_TEAM_KEY = "primary"

#: Long enough that a race decides inside it, short enough that a probe
#: that dies leaves nothing standing for long.
LEASE_SECONDS = 120.0

#: The lease case (c) waits out. The port takes the duration per call, so
#: this is the probe's choice and not a deployment's.
SHORT_LEASE_SECONDS = 60.0

#: How long one creation is made to take to reach the backend, and how
#: far past its own deadline the renewal that follows it lands. The
#: refuted arithmetic read the first as a clock offset and handed the
#: holder immunity worth exactly that much, so a renewal inside this
#: window is the interleaving that broke it.
#:
#: The window has to be wider than the two calls it takes to land that
#: renewal — the successor's claim and the lapsed holder's renewal — or
#: the renewal lands OUTSIDE the immunity the refuted arithmetic would
#: have granted and the case stops testing the fence at all. Measured
#: 2026-09-09 on the real board: those two calls put the renewal between
#: 5.5s and 12.5s past the deadline across three runs, so a seven-second
#: window failed to contain it twice.
LANDING_DELAY_SECONDS = 45.0

ORDER_MARKERS = 8
ORDER_READS = 3

#: The suite is hermetic by design: it deletes ambient ``KODEZART_``
#: variables and unbinds the working-directory dotenv, so the gate cannot
#: read a developer's live deployment. A probe that measures a DEPLOYMENT
#: therefore names the file it is measuring against.
DEPLOYMENT_ENV = Path(__file__).resolve().parents[2] / ".env"


def _deployment_config() -> AppConfig:
    if not DEPLOYMENT_ENV.is_file():
        pytest.skip(f"no deployment configuration at {DEPLOYMENT_ENV.name}")
    return AppConfig(_env_file=DEPLOYMENT_ENV)


def _ids(comment_keys: Sequence[str]) -> str:
    """Comment ids as the ledger prints them."""
    return "[" + ", ".join(comment_keys) + "]" if comment_keys else "[none]"


class SlowToLand:
    """Delay one creation on its way to the backend, when a case arms it.

    A write that takes seconds to land is the interleaving this probe
    exists to run: the backend stamps the marker that much after the
    holder began the call, so the difference between the two readings is
    the skew PLUS the delay. Armed for exactly one creation, so every
    other case dials the untouched session.
    """

    def __init__(self, through: ManagedMcpToolCaller) -> None:
        self._through = through
        self.arm_seconds: float = 0.0
        self.landed: float = 0.0

    async def call_tool(
        self, *, name: str, arguments: Mapping[str, object]
    ) -> McpToolResult:
        if name == "save_comment" and "id" not in arguments and self.arm_seconds:
            delay, self.arm_seconds = self.arm_seconds, 0.0
            began = datetime.now(UTC)
            await asyncio.sleep(delay)
            result = await self._through.call_tool(name=name, arguments=arguments)
            self.landed = (datetime.now(UTC) - began).total_seconds()
            return result
        return await self._through.call_tool(name=name, arguments=arguments)


@dataclass
class Ownership:
    """Two deployments over one board, and the issue they contend for."""

    first: TrackerPort
    second: TrackerPort
    caller: ManagedMcpToolCaller
    slow: SlowToLand
    issue_key: str
    run_id: str

    @property
    def holders(self) -> tuple[str, str]:
        return (f"probe-{self.run_id}-a", f"probe-{self.run_id}-b")

    async def markers(self) -> list[str]:
        """The comments this run has standing on the probe issue right now.

        Read back off the board rather than remembered from the writes: an
        id the probe kept would say what it meant to leave, and what the
        evidence needs is what the board actually carries.
        """
        return list(
            await _probe_comments(
                self.first, issue_key=self.issue_key, run_id=self.run_id
            )
        )

    def surface(self, marker: str | None = None) -> WritableSurface:
        ref = ScopeRef(kind=ScopeKind.ISSUE, key=self.issue_key)
        if marker is None:
            return WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=ref)
        return WritableSurface(kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker=marker)


def _probe_operation() -> OperationConfig:
    """The deployment's own operation, under a marker identity of the probe's.

    The deployment declares no marker prefixes at all, so nothing it runs
    could write an ownership marker; a probe-owned prefix supplies one and
    keeps every marker this run mints unmistakably the probe's.
    """
    config = _deployment_config()
    if config.operation_config is None:
        pytest.skip("no operation config is configured for this deployment")
    declared = load_operation_config(Path(config.operation_config))
    return declared.model_copy(
        update={"marker_prefixes": {"claim": PROBE_MARKER_PREFIX}}
    )


async def _dial(
    operation: OperationConfig,
    *,
    wrap: Callable[[ManagedMcpToolCaller], McpToolCaller] | None = None,
) -> tuple[TrackerPort, ManagedMcpToolCaller]:
    """The shipped adapter over a real session, and nothing else.

    Deliberately not the deployment boot: that reconciles every declared
    mapping, which writes to the board outside the one issue this probe is
    allowed to touch. The credential is still judged by the same shape rule
    boot applies before a request is made.
    """
    config = _deployment_config()
    if config.tracker.token is None:
        pytest.skip("no tracker credential is configured for this deployment")
    token = config.tracker.token.get_secret_value()
    refuse_foreign_credential(backend=config.tracker.backend, token=token)
    caller = make_mcp_tool_caller(settings=config.tracker, token=token)
    await caller.probe()
    await caller.open()
    tracker, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=operation,
        caller=caller if wrap is None else wrap(caller),
    )
    return tracker, caller


async def _probe_issue(tracker: TrackerPort) -> str:
    """The probe issue, found by its exact title or created once."""
    found = [
        issue
        for issue in await tracker.scan_issues(
            query=IssueQuery(team_key=PROBE_TEAM_KEY, page_size=250)
        )
        if issue.title == PROBE_TITLE
    ]
    if len(found) > 1:
        raise AssertionError(f"the board carries {len(found)} probe issues")
    if found:
        return found[0].issue_key
    created = await tracker.create_issue(
        title=PROBE_TITLE,
        body=PROBE_BODY,
        team_key=PROBE_TEAM_KEY,
        priority=IssuePriority.LOW,
    )
    return created.issue_key


async def _probe_comments(
    tracker: TrackerPort, *, issue_key: str, run_id: str
) -> Sequence[str]:
    """Every comment on the probe issue this run minted."""
    return [
        comment.comment_key
        for comment in await tracker.list_comments(issue_key=issue_key)
        if run_id in comment.body
    ]


async def _probe_litter(tracker: TrackerPort, *, issue_key: str) -> Sequence[str]:
    """Every marker the PROBE has ever minted here, whichever run minted it.

    A sweep that matched only its own run could not take off what a run
    that died mid-race left, and the litter then answers the next run's
    reads: measured on this board, abandoned markers from two earlier
    runs stood for hours because nothing but their own dead run was ever
    going to look for them.  Every ownership marker the probe writes
    carries the probe's own marker identity, which no deployment
    declares, and every plain comment it writes names the probe and the
    run that wrote it, so matching those takes the probe's litter and
    nothing else.
    """
    return [
        comment.comment_key
        for comment in await tracker.list_comments(issue_key=issue_key)
        if PROBE_MARKER_PREFIX in comment.body or PROBE_COMMENT.match(comment.body)
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def ownership() -> AsyncIterator[Ownership]:
    """Two adapter instances over two sessions, and a swept probe issue."""
    operation = _probe_operation()
    run_id = uuid.uuid4().hex[:8]
    slow: list[SlowToLand] = []

    def delaying(caller: ManagedMcpToolCaller) -> McpToolCaller:
        slow.append(SlowToLand(caller))
        return slow[-1]

    first, first_caller = await _dial(operation, wrap=delaying)
    second, second_caller = await _dial(operation)
    issue_key = await _probe_issue(first)
    assert issue_key, "the probe has no issue to write to"
    held = Ownership(
        first=first,
        second=second,
        caller=first_caller,
        slow=slow[0],
        issue_key=issue_key,
        run_id=run_id,
    )
    try:
        yield held
    finally:
        for holder in held.holders:
            with suppress(McpTransportError):
                await first.release_claim(issue_key=issue_key, holder=holder)
        left = sorted(
            {
                *await _probe_comments(first, issue_key=issue_key, run_id=run_id),
                *await _probe_litter(first, issue_key=issue_key),
            }
        )
        # Every deletion is attempted: one the backend turns down — a
        # comment a stale listing still answers with — would otherwise
        # abandon every marker after it, which is how this issue came to
        # carry litter from runs that died hours earlier.
        for comment_key in left:
            with suppress(McpTransportError):
                await first_caller.call_tool(
                    name="delete_comment", arguments={"id": comment_key}
                )
        remaining = sorted(
            {
                *await _probe_comments(first, issue_key=issue_key, run_id=run_id),
                *await _probe_litter(first, issue_key=issue_key),
            }
        )
        record(
            probe=PROBE,
            question="does the run leave the probe issue as it found it?",
            configuration=f"{issue_key}, run {run_id}",
            observed=(
                f"swept {len(left)} {_ids(left)}; "
                f"remaining {len(remaining)} {_ids(remaining)}"
            ),
            verdict="clean" if not remaining else "LEFT BEHIND",
        )
        await first_caller.close()
        await second_caller.close()
        assert remaining == [], f"the probe left {len(remaining)} markers behind"


async def test_created_at_order_is_stable_across_read_backs(
    ownership: Ownership,
) -> None:
    """The order everything else rests on: server-assigned, and it holds still."""
    written: list[datetime] = []
    for index in range(ORDER_MARKERS):
        before = datetime.now(UTC)
        await ownership.first.post_comment(
            issue_key=ownership.issue_key,
            body=f"probe {ownership.run_id} order {index}",
        )
        written.append(before)
    reads = []
    for _ in range(ORDER_READS):
        comments = [
            comment
            for comment in await ownership.first.list_comments(
                issue_key=ownership.issue_key
            )
            if f"probe {ownership.run_id} order" in comment.body
        ]
        reads.append([(one.comment_key, one.created_at) for one in comments])
    stamps = [instant for _, instant in reads[0]]
    skews = [
        (stamp - local).total_seconds()
        for stamp, local in zip(stamps, written, strict=False)
    ]
    digits = {len(f"{stamp.microsecond:06d}".rstrip("0")) for stamp in stamps}

    record(
        probe=PROBE,
        question="is comment creation order server-assigned and stable?",
        configuration=(
            f"{ownership.issue_key}, {ORDER_MARKERS} markers, {ORDER_READS} read-backs"
        ),
        observed=(
            f"created {_ids([key for key, _ in reads[0]])}; "
            f"identical order {sum(read == reads[0] for read in reads)}/{ORDER_READS}; "
            f"distinct instants {len(set(stamps))}/{len(stamps)}; "
            f"monotonic vs write order {stamps == sorted(stamps)}; "
            f"fractional digits {sorted(digits)}; "
            f"skew {min(skews):+.3f}s..{max(skews):+.3f}s"
        ),
        verdict="stable" if all(read == reads[0] for read in reads) else "UNSTABLE",
    )

    assert all(read == reads[0] for read in reads)
    assert stamps == sorted(stamps)
    for comment_key, _ in reads[0]:
        await ownership.caller.call_tool(
            name="delete_comment", arguments={"id": comment_key}
        )


async def test_two_claimants_race_to_exactly_one_grant(ownership: Ownership) -> None:
    holder_a, holder_b = ownership.holders
    outcomes = await asyncio.gather(
        ownership.first.claim_issue(
            issue_key=ownership.issue_key,
            holder=holder_a,
            lease_seconds=LEASE_SECONDS,
        ),
        ownership.second.claim_issue(
            issue_key=ownership.issue_key,
            holder=holder_b,
            lease_seconds=LEASE_SECONDS,
        ),
    )
    granted = [one for one in outcomes if one.status is ClaimStatus.GRANTED]
    refused = [one for one in outcomes if one.status is not ClaimStatus.GRANTED]
    held = await ownership.first.active_claim(issue_key=ownership.issue_key)
    standing = await ownership.markers()

    record(
        probe=PROBE,
        question="do two simultaneous claimants produce exactly one owner?",
        configuration=f"{ownership.issue_key}, two sessions, lease {LEASE_SECONDS:g}s",
        observed=(
            f"granted {[one.holder for one in granted]}; "
            f"refused {[(one.status.value, one.current_holder) for one in refused]}; "
            f"active claim {None if held is None else held.holder}; "
            f"markers standing {_ids(standing)}"
        ),
        verdict="one owner" if len(granted) == 1 else "NOT EXACTLY ONE",
    )

    # Released before the assertions: a case that fails still hands the
    # issue on, or every case after it measures this one's leftovers.
    for outcome in outcomes:
        await ownership.first.release_claim(
            issue_key=ownership.issue_key, holder=outcome.holder
        )

    assert len(granted) == 1
    assert len(refused) == 1
    if refused[0].status is ClaimStatus.LOST:
        assert refused[0].current_holder == granted[0].holder
        assert held is not None
        assert held.holder == granted[0].holder
    else:
        assert refused[0].status is ClaimStatus.CONTENDED


async def test_a_delayed_renewal_after_expiry_reports_lost(
    ownership: Ownership,
) -> None:
    """The counterexample, on the real board: A expires, B takes it, A renews."""
    holder_a, holder_b = ownership.holders
    granted = await ownership.first.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=SHORT_LEASE_SECONDS,
    )
    assert granted.status is ClaimStatus.GRANTED
    await asyncio.sleep(SHORT_LEASE_SECONDS + 2)
    replacement = await ownership.second.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_b,
        lease_seconds=LEASE_SECONDS,
    )
    stale = await ownership.first.renew_claim(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=LEASE_SECONDS,
    )
    held = await ownership.second.active_claim(issue_key=ownership.issue_key)
    standing = await ownership.markers()

    record(
        probe=PROBE,
        question="can a renewal delayed past its expiry take the issue back?",
        configuration=(
            f"{ownership.issue_key}, lease {SHORT_LEASE_SECONDS:g}s, "
            "waited past it, other holder claimed"
        ),
        observed=(
            f"replacement {replacement.status.value}; "
            f"stale renewal {'None' if stale is None else stale.status.value}; "
            f"active claim {None if held is None else held.holder}; "
            f"markers standing {_ids(standing)}"
        ),
        verdict="no restore" if stale is None else "RESTORED",
    )

    await ownership.second.release_claim(issue_key=ownership.issue_key, holder=holder_b)

    assert replacement.status is ClaimStatus.GRANTED
    assert stale is None
    assert held is not None
    assert held.holder == holder_b
    # The lapsed holder took its own marker down on the way out, so the
    # board advertises exactly the one holder that owns the issue.
    assert len(standing) == 1


async def test_an_edit_keeps_its_place_and_moves_the_stamp_that_dates_it(
    ownership: Ownership,
) -> None:
    """The raw material of the fence, measured rather than assumed.

    Ownership is decided by the backend's own stamps: the creation a
    marker is ordered by, and the update its last write is dated by. A
    backend that moved the first, or did not move the second, could not
    carry the arithmetic at all. Read off the vendor's own payload rather
    than the port's projection, which keeps only the creation.
    """
    written = await ownership.first.post_comment(
        issue_key=ownership.issue_key, body=f"probe {ownership.run_id} edit before"
    )

    async def stamps() -> tuple[datetime, datetime]:
        answer = await ownership.caller.call_tool(
            name="list_comments", arguments={"issueId": ownership.issue_key}
        )
        assert isinstance(answer, Mapping)
        entries = answer["comments"]
        assert isinstance(entries, list)
        found = [
            entry
            for entry in entries
            if isinstance(entry, Mapping) and entry["id"] == written.comment_key
        ]
        assert len(found) == 1
        return (
            datetime.fromisoformat(str(found[0]["createdAt"])),
            datetime.fromisoformat(str(found[0]["updatedAt"])),
        )

    created, dated = await stamps()
    await asyncio.sleep(1)
    await ownership.caller.call_tool(
        name="save_comment",
        arguments={
            "id": written.comment_key,
            "body": f"probe {ownership.run_id} edit after",
        },
    )
    created_after, dated_after = await stamps()

    record(
        probe=PROBE,
        question="does an edit keep a comment's place and move its stamp?",
        configuration=f"{ownership.issue_key}, one comment, one edit by id",
        observed=(
            f"comment {written.comment_key}; "
            f"createdAt kept {created_after == created}; "
            f"updatedAt moved {dated_after > dated} by "
            f"{(dated_after - dated).total_seconds():+.3f}s; "
            f"creation to first update {(dated - created).total_seconds():+.3f}s"
        ),
        verdict=(
            "orderable"
            if created_after == created and dated_after > dated
            else "NOT ORDERABLE"
        ),
    )

    assert created_after == created
    assert dated_after > dated
    await ownership.caller.call_tool(
        name="delete_comment", arguments={"id": written.comment_key}
    )


async def test_a_grant_that_lands_late_gains_no_immunity_from_the_fence(
    ownership: Ownership,
) -> None:
    """The interleaving that refuted the round before this one, run live.

    A's own creation is made to take seconds to reach the backend, so the
    backend's reading of that write is the skew PLUS that delay away from
    A's. The refuted arithmetic read the difference as a clock offset and
    let A's renewal land that much past its deadline and still be in
    force. Here the renewal is deliberately landed INSIDE that window:
    after the deadline the creation bought, and before the deadline plus
    the delay. It must renew nothing, and B must be the only owner.
    """
    holder_a, holder_b = ownership.holders
    ownership.slow.arm_seconds = LANDING_DELAY_SECONDS
    began = datetime.now(UTC)
    granted = await ownership.first.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=SHORT_LEASE_SECONDS,
    )
    assert granted.status is ClaimStatus.GRANTED
    stamped = [
        comment.created_at
        for comment in await ownership.first.list_comments(
            issue_key=ownership.issue_key
        )
        if ownership.run_id in comment.body
    ]
    assert len(stamped) == 1
    landed = (stamped[0] - began).total_seconds()
    deadline = stamped[0] + timedelta(seconds=SHORT_LEASE_SECONDS)
    await asyncio.sleep(max((deadline - datetime.now(UTC)).total_seconds(), 0.0) + 1.0)
    replacement = await ownership.second.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_b,
        lease_seconds=LEASE_SECONDS,
    )
    stale = await ownership.first.renew_claim(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=LEASE_SECONDS,
    )
    late = (datetime.now(UTC) - deadline).total_seconds()
    held = await ownership.second.active_claim(issue_key=ownership.issue_key)
    standing = await ownership.markers()

    record(
        probe=PROBE,
        question="does a grant whose creation lands late gain fence immunity?",
        configuration=(
            f"{ownership.issue_key}, creation delayed {LANDING_DELAY_SECONDS:g}s, "
            f"lease {SHORT_LEASE_SECONDS:g}s, renewal landed inside the "
            "window the refuted arithmetic would have granted"
        ),
        observed=(
            f"creation landed {landed:+.3f}s after the call began; "
            f"replacement {replacement.status.value}; "
            f"renewal {late:+.3f}s past the deadline "
            f"(immunity window {LANDING_DELAY_SECONDS:g}s) -> "
            f"{'None' if stale is None else stale.status.value}; "
            f"active claim {None if held is None else held.holder}; "
            f"markers standing {_ids(standing)}"
        ),
        verdict="no immunity" if stale is None else "IMMUNE",
    )

    await ownership.second.release_claim(issue_key=ownership.issue_key, holder=holder_b)

    assert landed >= LANDING_DELAY_SECONDS
    assert 0.0 < late < LANDING_DELAY_SECONDS
    assert replacement.status is ClaimStatus.GRANTED
    assert stale is None
    assert held is not None
    assert held.holder == holder_b
    assert len(standing) == 1


async def test_intersecting_lease_refused_disjoint_granted(
    ownership: Ownership,
) -> None:
    holder_a, holder_b = ownership.holders
    taken = frozenset({ownership.surface(), ownership.surface("probe")})
    lease = await ownership.first.acquire_surfaces(
        surfaces=taken, holder=holder_a, lease_seconds=LEASE_SECONDS
    )
    with pytest.raises(SurfaceLeaseError) as refused:
        await ownership.second.acquire_surfaces(
            surfaces=frozenset({ownership.surface("probe")}),
            holder=holder_b,
            lease_seconds=LEASE_SECONDS,
        )
    disjoint = await ownership.second.acquire_surfaces(
        surfaces=frozenset({ownership.surface("probe-b")}),
        holder=holder_b,
        lease_seconds=LEASE_SECONDS,
    )

    standing = await ownership.markers()

    record(
        probe=PROBE,
        question="does a lease exclude an intersecting set and admit a disjoint one?",
        configuration=(
            f"{ownership.issue_key}, two sessions, description + two marker comments"
        ),
        observed=(
            f"held by {lease.holder}; "
            f"intersecting refused naming {refused.value.current_holder}; "
            f"disjoint granted to {disjoint.holder}; "
            f"markers standing {_ids(standing)}"
        ),
        verdict=(
            "exclusive"
            if refused.value.current_holder == holder_a and disjoint.holder == holder_b
            else "WRONG OWNER"
        ),
    )

    assert refused.value.current_holder == holder_a
    assert refused.value.marker == "probe"
    assert disjoint.holder == holder_b


async def test_release_frees_for_the_next_holder(ownership: Ownership) -> None:
    holder_a, holder_b = ownership.holders
    taken = frozenset({ownership.surface(), ownership.surface("probe")})
    await ownership.first.release_surfaces(surfaces=taken, holder=holder_a)
    inherited = await ownership.second.acquire_surfaces(
        surfaces=taken, holder=holder_b, lease_seconds=LEASE_SECONDS
    )
    await ownership.second.release_surfaces(surfaces=taken, holder=holder_b)
    await ownership.second.release_surfaces(
        surfaces=frozenset({ownership.surface("probe-b")}), holder=holder_b
    )
    claimed = await ownership.first.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=LEASE_SECONDS,
    )
    await ownership.first.release_claim(issue_key=ownership.issue_key, holder=holder_a)
    unclaimed = await ownership.first.active_claim(issue_key=ownership.issue_key)
    standing = await ownership.markers()

    record(
        probe=PROBE,
        question="does a release hand the surfaces and the issue on?",
        configuration=(
            f"{ownership.issue_key}, release then acquire, release then claim"
        ),
        observed=(
            f"inherited by {inherited.holder}; "
            f"claim after release {claimed.status.value}; "
            f"active claim after release {unclaimed}; "
            f"markers standing {_ids(standing)}"
        ),
        verdict="freed" if unclaimed is None else "STILL HELD",
    )

    assert inherited.holder == holder_b
    assert claimed.status is ClaimStatus.GRANTED
    assert unclaimed is None


async def test_a_redeployed_process_claims_what_its_predecessor_holds(
    ownership: Ownership,
) -> None:
    """One deployment identity on two sessions, both claiming at once.

    The realistic restart, and the interleaving that refuted the round
    before this one: a process claims an issue its predecessor already
    holds, both calls in flight together. A holder identity is what the
    arbitration is over, so the two grants are one ownership observed
    twice rather than a tie to break: both must be granted, the issue
    must read as that holder's, another deployment must be refused in
    its name, and the holder must still be able to renew what it holds —
    what may never happen is the annihilation, where both grants retract
    each other and the identity is left owning nothing.

    Everything still standing must be this holder's. How MANY markers is
    measured rather than asserted: a backend with no conditional write
    can hide each grant's confirmation from the other's read, and what
    that leaves is a duplicate marker rather than a second owner — every
    reader names one holder, and the marker nothing renews lapses.
    """
    holder_a, holder_b = ownership.holders
    before = await ownership.markers()
    outcomes = await asyncio.gather(
        ownership.first.claim_issue(
            issue_key=ownership.issue_key,
            holder=holder_a,
            lease_seconds=LEASE_SECONDS,
        ),
        ownership.second.claim_issue(
            issue_key=ownership.issue_key,
            holder=holder_a,
            lease_seconds=LEASE_SECONDS,
        ),
    )
    standing = await ownership.markers()
    bodies = [
        comment.body
        for comment in await ownership.first.list_comments(
            issue_key=ownership.issue_key
        )
        if ownership.run_id in comment.body
    ]
    held = await ownership.first.active_claim(issue_key=ownership.issue_key)
    rival = await ownership.second.claim_issue(
        issue_key=ownership.issue_key,
        holder=holder_b,
        lease_seconds=LEASE_SECONDS,
    )
    renewed = await ownership.first.renew_claim(
        issue_key=ownership.issue_key,
        holder=holder_a,
        lease_seconds=LEASE_SECONDS,
    )

    record(
        probe=PROBE,
        question="does one holder claiming twice at once end up holding the issue?",
        configuration=(
            f"{ownership.issue_key}, two sessions under ONE holder, "
            f"lease {LEASE_SECONDS:g}s, board carried {len(before)} markers before"
        ),
        observed=(
            f"granted {[one.status.value for one in outcomes]}; "
            f"markers standing {_ids(standing)}; "
            f"active claim {None if held is None else held.holder}; "
            f"rival {rival.status.value} naming {rival.current_holder}; "
            f"renewal {'None' if renewed is None else renewed.status.value}"
        ),
        verdict=(
            "one ownership" if standing and renewed is not None else "ANNIHILATED"
        ),
    )

    await ownership.first.release_claim(issue_key=ownership.issue_key, holder=holder_a)
    freed = await ownership.first.active_claim(issue_key=ownership.issue_key)

    assert [one.status for one in outcomes] == [ClaimStatus.GRANTED] * 2
    assert standing
    assert all(holder_a in body and holder_b not in body for body in bodies)
    assert held is not None
    assert held.holder == holder_a
    assert rival.status is ClaimStatus.LOST
    assert rival.current_holder == holder_a
    assert renewed is not None
    assert renewed.status is ClaimStatus.GRANTED
    # One release ends it: the duplicate a hidden confirmation can leave
    # is this holder's too, and a release takes every marker of its own.
    assert freed is None
