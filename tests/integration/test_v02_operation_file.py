"""A v0.2 operation file boots as it is (KOD-903).

The file is the v0.2.0 example exactly as it shipped, read from the object
store by its blob id and never copied into the tree: it names no marker table
and carries an initiative roster. It boots, schedules the per-issue passes, and
every purpose the per-issue path needs is answered by the markers v0.2 wrote.
That example declares no fire log, so the fire-log cases build one in v0.2's
``RecordDestination`` shape: neither ``columns`` nor ``outcome_mapping``.
"""

import asyncio
import re
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from kodezart.adapters.linear.markers import LinearMarkers
from kodezart.adapters.notion.record_sink import NotionRecordSink
from kodezart.adapters.toml_operation_config import read_operation_file
from kodezart.composition.knowledge import fire_record_template
from kodezart.composition.passes import (
    build_dispatch_passes,
    build_dispatch_runtime,
    verify_pass_preflight,
)
from kodezart.composition.tracker import DialledTracker
from kodezart.config.app import AppConfig
from kodezart.core.errors import TrackerProtocolError
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.run_recorder import RunRecorder
from kodezart.types.domain.branch import BaseSpec, WorkRefLanding, WorkRefRole
from kodezart.types.domain.dispatch import ExclusionClause, SelfWriteLedger
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationMemberAbsentError,
    RecordColumns,
    RecordDestination,
    RecordDurationUnit,
    RecordOutcomeMapping,
    RunKind,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunRecordResult
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.tracker import ClaimStatus
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeMcpComment,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    ManagedFakeLinearMcpServer,
    PassThroughGate,
)
from tests.probes.notion_records import NotionLogServer
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_dispatch_pass import (
    INTEGRATION_DIR,
    PRIMARY_REPO,
    operation_config,
)
from tests.services.test_fire_dispatcher import dispatcher
from tests.services.test_prompt_passes import _config
from tests.services.test_run_recorder import _record
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    fixture_server,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over

#: ``docs/operation.example.toml`` as the v0.2.0 tag shipped it
#: (``git rev-parse v0.2.0:docs/operation.example.toml``).
V020_EXAMPLE_BLOB = "a4206034a83268c36c68d62c1f5b829cd2f303c6"

#: The markers v0.2.0 wrote, spelled from its own wire forms rather than read
#: off the loader's table, plus the shipped example's outcome marker.
V02_WIRE_PREFIXES = {
    "claim": "kodezart-claim",
    "work_ref": "kodezart-workref",
    "base_spec": "kodezart-basespec",
    "repository": "kodezart-repo",
    "run_outcome": "run-outcome",
}

#: Bounded because a tick that hangs is a failure, not a wait.
TICK_BOUND_SECONDS = 30


def v020_example(tmp_path: Path) -> Path:
    """The v0.2.0 example's bytes, unchanged, as a file an operator points at."""
    fetched = subprocess.run(
        ["git", "cat-file", "blob", V020_EXAMPLE_BLOB],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    if fetched.returncode != 0:
        pytest.fail(
            f"the v0.2.0 example (blob {V020_EXAMPLE_BLOB}) is not in this "
            "clone's object store; run `git fetch --tags` and run again"
        )
    body = fetched.stdout
    assert re.search(rb"^\[\[initiatives\]\]$", body, re.MULTILINE)
    assert not any(line.strip() == b"[marker_prefixes]" for line in body.splitlines())
    path = tmp_path / "operation.toml"
    path.write_bytes(body)
    return path


async def test_the_v020_example_boots_unchanged_and_schedules_the_per_issue_passes(
    tmp_path,
):
    loaded = read_operation_file(v020_example(tmp_path))
    assert loaded.ignored == ("initiatives",)
    assert loaded.defaulted == ("marker_prefixes",)
    operation = loaded.config
    assert operation.marker_prefixes == V02_WIRE_PREFIXES

    # As the composition root does it: one registry bound to the operation,
    # preflight, then the wiring.
    config = _config(tmp_path)
    prompts = load_registry(bindings=operation_bindings(operation))
    board = FakeTrackerPort()
    forge = FakeDeliveryProbe()
    queue = FakeJobQueue()
    await verify_pass_preflight(
        config=config,
        operation=operation,
        tracker=board,
        github_api=forge,
        prompts=prompts,
    )
    runtime = await build_dispatch_runtime(
        config=config,
        operation=operation,
        dialled=DialledTracker(
            tracker=board,
            caller=ManagedFakeLinearMcpServer(),
            operation=operation,
            ledger=board.self_writes,
            status=FakeScopeStatusWriter(),
        ),
        github_api=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        workspace=FakeWorkspaceProvider(),
        prompts=prompts,
        runner=FakeAgentRunner(events=[]),
        skills=SUPPRESS_ALL_SKILLS,
        recorder=RunRecorder(records={}, sinks={}),
        log=get_logger(__name__),
    )

    assert [entry.name for entry in runtime.scheduler.passes] == [
        *(f"dispatch:{repo.url}" for repo in operation.repos),
        "fire_prep_pass",
        "grooming_pass",
    ]
    assert len(operation.repos) == 2
    # The lifecycle writer, which needs the outcome marker, was constructed.
    assert runtime.lifecycle is not None


async def test_a_v020_file_claims_dispatches_and_records_its_outcome(tmp_path):
    """Every purpose the per-issue path writes under, answered by the defaults.

    Over the real tracker adapter and the fixture workspace, the operation's
    board carrying the loaded file's markers: a purpose the defaults left out
    would refuse here, by name, before anything is written.
    """
    prefixes = read_operation_file(v020_example(tmp_path)).config.marker_prefixes
    server = fixture_server()
    # The fixture's other approved issue is blocked by this one; unlabel it so
    # the tick's winner is the one issue this case is about.
    server.issues[APPROVED_ISSUE].labels = []
    ledger = SelfWriteLedger()
    tracker = tracker_over(server, marker_prefixes=prefixes, ledger=ledger)
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        recorder=RunRecorder(records={}, sinks={}),
        config=AppConfig(_env_file=None, dispatch_pass_gate_signals=[]),
        operation=operation_config().model_copy(update={"marker_prefixes": prefixes}),
        tracker=tracker,
        ledger=ledger,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    async with asyncio.timeout(TICK_BOUND_SECONDS):
        await built.passes[0].run(FIXTURE_NOW)

    ((_lane, submission),) = queue.submissions
    assert submission.issue_key == CLAIMED_ISSUE
    assert submission.scope is None
    bodies = [comment.body for comment in server.comments]
    assert any(body.startswith("```kodezart-claim\n") for body in bodies)
    assert any(body.startswith("<!-- kodezart-basespec ") for body in bodies)

    await built.lifecycle._writer.on_terminal_outcome(
        issue_key=CLAIMED_ISSUE,
        job_id="job-0001",
        outcome=WorkflowOutcome.review_passed_no_pr_adapter,
    )

    outcomes = [
        comment.body
        for comment in server.comments
        if comment.body.startswith("[run-outcome:")
    ]
    assert outcomes == [
        f"[run-outcome:{CLAIMED_ISSUE}:job-0001]\n"
        "job job-0001 reached outcome review_passed_no_pr_adapter"
    ]


@pytest.mark.parametrize(
    ("table", "declared"),
    [('run_outcome = "x"\n', {"run_outcome": "x"}), ("", {})],
    ids=["one-purpose", "empty"],
)
async def test_a_declared_marker_table_is_taken_as_written(tmp_path, table, declared):
    """A declared table, even an empty one, is never extended by the defaults."""
    path = tmp_path / "operation.toml"
    path.write_text(
        f'operation_name = "o"\nworkspace = "w"\n\n[marker_prefixes]\n{table}',
        encoding="utf-8",
    )

    loaded = read_operation_file(path)

    assert loaded.config.marker_prefixes == declared
    assert loaded.defaulted == ()
    assert loaded.ignored == ()
    with pytest.raises(OperationMemberAbsentError) as refused:
        _ = LinearMarkers(loaded.config.marker_prefixes).grant_pattern
    assert "claim" in refused.value.missing


#: Comment bodies exactly as v0.2.0's writers produced them.
V02_BASE_SPEC = BaseSpec(inputs=(), base_branch="release")
LIVE = FIXTURE_NOW + timedelta(minutes=10)
LATER = FIXTURE_NOW + timedelta(minutes=20)
LATEST = FIXTURE_NOW + timedelta(minutes=30)
LAPSED = FIXTURE_NOW - timedelta(minutes=1)


def _v02_claim(holder, expires_at):
    return (
        f'<!-- kodezart-claim holder="{holder}" '
        f'expires-at="{expires_at.isoformat()}" -->'
    )


def _comment(key, body, *, created_at=FIXTURE_NOW - timedelta(hours=1)):
    return FakeMcpComment(
        id=key,
        issue_id=CLAIMED_ISSUE,
        author="Kodezart",
        body=body,
        created_at=created_at,
    )


def _board(*comments):
    server = fixture_server()
    server.issues[APPROVED_ISSUE].labels = []
    server.comments.extend(comments)
    return server, tracker_over(
        server,
        marker_prefixes=V02_WIRE_PREFIXES,
        clock=lambda: FIXTURE_NOW,
        ledger=SelfWriteLedger(),
    )


def _comment_reads(server):
    return [name for name, _ in server.calls if name == "list_comments"]


async def test_markers_v02_wrote_are_still_read():
    # A live v0.2 claim holds the issue, with its holder and its expiry.
    server, tracker = _board(_comment("c-1", _v02_claim("v02-host", LIVE)))
    claim = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert claim is not None
    assert claim.status is ClaimStatus.GRANTED
    assert claim.holder == "v02-host"
    assert claim.expires_at == LIVE
    assert len(_comment_reads(server)) == 1

    # A lapsed one holds nothing.
    _server, tracker = _board(_comment("c-1", _v02_claim("v02-host", LAPSED)))
    assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None

    # Two holders: the earliest created wins, until its own latest expiry,
    # which is not the later expiry the other holder's claim carries.
    early = FIXTURE_NOW - timedelta(hours=2)
    _server, tracker = _board(
        _comment("c-2", _v02_claim("second", LATEST)),
        _comment("c-1", _v02_claim("first", LIVE), created_at=early),
        _comment(
            "c-3",
            _v02_claim("first", LATER),
            created_at=early + timedelta(minutes=1),
        ),
    )
    claim = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert claim is not None
    assert (claim.holder, claim.expires_at) == ("first", LATER)

    # A live grant written today wins over a live v0.2 claim beside it.
    _server, tracker = _board(_comment("c-1", _v02_claim("v02-host", LATER)))
    granted = await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="today", lease_seconds=900
    )
    assert granted.status is ClaimStatus.GRANTED
    claim = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert claim is not None
    assert claim.holder == "today"

    # The other markers v0.2 wrote read as they are.
    server, tracker = _board(
        _comment(
            "w-1",
            '<!-- kodezart-workref role="deliverable" branch="kz/deliverable" '
            f'pushed-head-sha="{"a" * 40}" -->',
        ),
        _comment(
            "b-1",
            "<!-- kodezart-basespec "
            f"{V02_BASE_SPEC.model_dump_json(by_alias=True)} -->",
        ),
        _comment("r-1", '<!-- kodezart-repo url="https://example.invalid/a/repo" -->'),
    )
    (ref,) = await tracker.work_refs(issue_key=CLAIMED_ISSUE)
    assert ref.role is WorkRefRole.DELIVERABLE
    assert ref.branch == "kz/deliverable"
    assert ref.pushed_head_sha == "a" * 40
    assert ref.landing is WorkRefLanding.UNKNOWN
    assert await tracker.read_base_spec(issue_key=CLAIMED_ISSUE) == V02_BASE_SPEC
    assert (
        await tracker.recorded_repository(issue_key=CLAIMED_ISSUE)
        == "https://example.invalid/a/repo"
    )

    # And the per-issue dispatcher honours a live v0.2 claim.
    _server, tracker = _board(_comment("c-1", _v02_claim("v02-host", LIVE)))
    fire, queue, _ = dispatcher(
        tracker,
        operation=operation_config().model_copy(
            update={"marker_prefixes": V02_WIRE_PREFIXES}
        ),
        repo_url=PRIMARY_REPO,
    )
    async with asyncio.timeout(TICK_BOUND_SECONDS):
        report = await fire.run_pass()
    assert [
        (exclusion.issue_key, exclusion.clause, exclusion.detail)
        for exclusion in report.exclusions
        if exclusion.issue_key == CLAIMED_ISSUE
    ] == [(CLAIMED_ISSUE, ExclusionClause.CLAIMED_OR_IN_FLIGHT, "v02-host")]
    assert queue.submissions == []


@pytest.mark.parametrize(
    "expires_at",
    [
        "not-an-instant",
        (FIXTURE_NOW + timedelta(minutes=10)).replace(tzinfo=None).isoformat(),
    ],
    ids=["unparseable", "zoneless"],
)
async def test_a_v02_claim_whose_expiry_cannot_be_read_is_refused(expires_at):
    """An expiry that does not parse, or names no zone, is a malformed marker."""
    body = f'<!-- kodezart-claim holder="v02-host" expires-at="{expires_at}" -->'
    _server, tracker = _board(_comment("c-1", body))

    with pytest.raises(TrackerProtocolError) as refused:
        await tracker.active_claim(issue_key=CLAIMED_ISSUE)

    assert "ownership marker does not carry the fields" in str(refused.value)
    assert "comment=c-1" in str(refused.value)


async def test_a_v02_claim_expiring_at_the_reading_instant_has_lapsed():
    """v0.2's own rule: a claim whose expiry is not after now holds nothing."""
    _server, tracker = _board(_comment("c-1", _v02_claim("v02-host", FIXTURE_NOW)))

    assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None


async def test_a_fenced_tie_beside_a_live_v02_claim_reads_unclaimed():
    """Two live grants at one instant are not settled by a v0.2 claim (D15)."""
    server, tracker = _board(_comment("c-1", _v02_claim("v02-host", LATER)))
    granted = await tracker.claim_issue(
        issue_key=CLAIMED_ISSUE, holder="runner-a", lease_seconds=900
    )
    assert granted.status is ClaimStatus.GRANTED
    (grant,) = [
        comment for comment in server.comments if "holder: runner-a" in comment.body
    ]
    # A second holder's grant stamped at the same instant as the first.
    server.comments.append(
        replace(
            grant,
            id=f"{grant.id}-tied",
            body=grant.body.replace("holder: runner-a", "holder: runner-b"),
        )
    )

    assert await tracker.active_claim(issue_key=CLAIMED_ISSUE) is None


def test_the_v02_claim_prefix_is_matched_as_written():
    """A prefix carrying a pattern character matches only itself."""
    pattern = LinearMarkers({"claim": "kz.claim"}).v02_claim_pattern
    expiry = LIVE.isoformat()

    assert pattern.search(f'<!-- kz.claim holder="h" expires-at="{expiry}" -->')
    assert not pattern.search(f'<!-- kzXclaim holder="h" expires-at="{expiry}" -->')


def _knowledge():
    return AppConfig(
        _env_file=None,
        knowledge={
            "session_grants": (SessionType.TICKET_FIRE,),
            "connection": {
                "transport": "http",
                "server_url": "https://knowledge.invalid/mcp",
                "credential": SecretStr("ntn_" + "F" * 44),
            },
        },
    ).knowledge


def _log(**structure):
    return RecordDestination(
        system=DocumentSystem.KNOWLEDGE,
        name="Fire Log",
        id="destination-1",
        append_only=True,
        **structure,
    )


COLUMNS = RecordColumns(
    repo="Repository",
    pr_url="Pull request",
    base_branch="Base",
    started="Began",
    ended="Ended",
    duration="Minutes",
    duration_unit=RecordDurationUnit.MINUTES,
    iterations="Iterations",
    what_happened="What happened",
)


_FIRE_LOG_TOML = """\
operation_name = "o"
workspace = "w"

[records.fire]
system = "knowledge"
name = "Fire Log"
id = "destination-1"
append_only = true
"""


@pytest.mark.parametrize(
    ("structure", "reported"),
    [
        ("", True),
        (
            "\n[records.fire.columns]\n"
            + "".join(
                f'{name} = "{value}"\n'
                for name, value in COLUMNS.model_dump(mode="json").items()
                if value is not None and name != "repo_options"
            ),
            False,
        ),
    ],
    ids=["no-structure", "columns"],
)
async def test_a_fire_log_given_the_v02_shape_is_reported_at_load(
    tmp_path, structure, reported
):
    """The loader names a knowledge fire log it will write the v0.2 way."""
    path = tmp_path / "operation.toml"
    path.write_text(_FIRE_LOG_TOML + structure, encoding="utf-8")

    loaded = read_operation_file(path)

    assert ("records.fire" in loaded.defaulted) is reported
    assert (
        loaded.config.records[RunKind.FIRE.value].records_structured(RunKind.FIRE)
        is not reported
    )


async def test_a_fire_log_with_no_structure_writes_the_v02_row_and_adds_no_clause(
    tmp_path,
):
    operation = read_operation_file(v020_example(tmp_path)).config
    v02_log = _log()
    declared = operation.model_copy(
        update={"records": {**operation.records, RunKind.FIRE.value: v02_log}}
    )
    prompts = load_registry(bindings=operation_bindings(declared))
    assert (
        fire_record_template(
            knowledge=_knowledge(), operation=declared, prompts=prompts
        )
        is None
    )

    # The row is the record's title line, exactly as v0.2 wrote it.
    server = NotionLogServer()
    record = _record(RunKind.FIRE, name="fire")
    sink = NotionRecordSink(caller=server, server_name="fixture")
    recorder = RunRecorder(
        records={RunKind.FIRE.value: v02_log},
        sinks={DocumentSystem.KNOWLEDGE: sink},
    )
    assert await recorder.record(record) is RunRecordResult.WRITTEN
    ((tool, _arguments),) = server.writes()
    assert tool == "API-post-page"
    (row,) = server.rows.values()
    assert row["properties"]["Run"]["title"] == [{"plain_text": record.line()}]

    # Declared structure keeps its rules: a grooming log with columns alone
    # still takes the title line, and a fire log with an outcome mapping and
    # no columns still refuses the clause by name.
    server = NotionLogServer()
    grooming = _record(RunKind.GROOMING, name="grooming_pass")
    recorder = RunRecorder(
        records={RunKind.GROOMING.value: _log(columns=COLUMNS)},
        sinks={
            DocumentSystem.KNOWLEDGE: NotionRecordSink(
                caller=server, server_name="fixture"
            )
        },
    )
    assert await recorder.record(grooming) is RunRecordResult.WRITTEN
    (row,) = server.rows.values()
    assert row["properties"]["Run"]["title"] == [{"plain_text": grooming.line()}]
    mapped = operation.model_copy(
        update={
            "records": {
                **operation.records,
                RunKind.FIRE.value: _log(
                    outcome_mapping=RecordOutcomeMapping(
                        property="Disposition", options={"run.completed": "Done"}
                    )
                ),
            }
        }
    )
    with pytest.raises(OperationMemberAbsentError) as refused:
        fire_record_template(knowledge=_knowledge(), operation=mapped, prompts=prompts)
    assert refused.value.missing == "records.fire.columns"
