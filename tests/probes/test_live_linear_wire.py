"""The Linear wire models, measured against the connected MCP.

Each call below is one the adapter makes, in the argument shape the adapter
sends, and each answer is validated against every wire model the adapter
validates that tool's answer with.  Twice on 2026-09-24 a wire model
disagreed with the live vendor — a top-level project or initiative answered
its display identifier under ``id`` and its UUID under ``uuid`` (e0f5167e),
and a root issue's ``get_issue`` answer carried no ``parentId`` key
(59493efc) — and each drift cost a boot cycle to find, because nothing in
the tree measured the models against the vendor.  This does, before a boot.

The last case reads the key's remaining hourly request budget off the
GraphQL API's rate-limit headers, for the same reason: a boot on a spent
key loses its tracker within seconds (KOD-1237).

Reads only.  Live only (``pytest -m live``): it dials the deployment's
tracker with the deployment's credential, read the way every live probe
reads it (``tests/probes/deployment.py``), and skips when there is none.
The subjects are the operator's, named in the environment under a prefix
the suite leaves alone (``tests/conftest.py`` deletes every ``KODEZART_``
variable at import)::

    LINEAR_PROBE_PROJECT           a project's UUID
    LINEAR_PROBE_ROOT_ISSUE        the key of an issue in it with no parent
    LINEAR_PROBE_CRITERION_ISSUE   the key of a sub-issue of that root
    LINEAR_PROBE_INITIATIVE        an initiative's UUID

Every call's latency and every model's verdict land in the probe ledger
(``tests/probes/recording.py``), printed once at the end of the run.
"""

import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

from kodezart.adapters.linear import scope_types, wire
from kodezart.adapters.linear.wire import LinearWireModel
from kodezart.composition.tracker import (
    make_mcp_tool_caller,
    refuse_foreign_credential,
)
from kodezart.config.app import AppConfig
from kodezart.core.protocols import ManagedMcpToolCaller, McpToolResult
from tests.probes.deployment import deployment_config
from tests.probes.recording import record

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

PROBE = "live-linear-wire"

#: The vendor's GraphQL endpoint, which the adapter never dials.  The budget
#: the MCP server spends is accounted there, and only its answers carry the
#: rate-limit headers.  An API key goes raw in the ``Authorization`` header.
GRAPHQL_URL = "https://api.linear.app/graphql"

#: Measured 2026-09-24 (KOD-1237): one key has 2,500 API requests an hour,
#: and one MCP tool call spends about two of them.
REQUESTS_PER_TOOL_CALL = 2

#: One page is enough to measure a listing's shape; the size changes none.
PAGE = 50

SUBJECT_VARIABLES = (
    "LINEAR_PROBE_PROJECT",
    "LINEAR_PROBE_ROOT_ISSUE",
    "LINEAR_PROBE_CRITERION_ISSUE",
    "LINEAR_PROBE_INITIATIVE",
)


@dataclass(frozen=True)
class Subjects:
    """What the operator points the probe at, one per subject variable."""

    project: str
    root_issue: str
    criterion_issue: str
    initiative: str


@dataclass(frozen=True)
class WireCase:
    """One adapter call, and every model the adapter reads its answer with."""

    label: str
    tool: str
    arguments: Callable[[Subjects], Mapping[str, object]]
    models: tuple[type[LinearWireModel], ...]


#: The adapter's own argument shapes (``adapters/linear/tracker.py`` and
#: ``scope_reader.py``), each paired with every model that validates the
#: tool's answer somewhere in the adapter.  Milestones are measured by the
#: test below them: the milestone to read comes off the board.
CASES = (
    WireCase(
        "root issue",
        "get_issue",
        lambda s: {"id": s.root_issue, "includeRelations": True},
        (
            scope_types.LinearApprovalIssueWire,
            wire.LinearAddressedIssueWire,
            wire.LinearIssueDetailWire,
            wire.LinearPlanningIssueWire,
            wire.LinearIssueStateHistoryWire,
        ),
    ),
    WireCase(
        "criterion",
        "get_issue",
        lambda s: {"id": s.criterion_issue, "includeRelations": True},
        (
            scope_types.LinearApprovalIssueWire,
            wire.LinearCriterionIssueWire,
            wire.LinearAddressedIssueWire,
        ),
    ),
    WireCase(
        "project issues",
        "list_issues",
        lambda s: {"project": s.project, "includeArchived": True, "limit": PAGE},
        (scope_types.LinearScopeIssuesWire, wire.LinearIssueListWire),
    ),
    WireCase(
        "children",
        "list_issues",
        lambda s: {"parentId": s.root_issue, "includeArchived": True, "limit": PAGE},
        (scope_types.LinearScopeIssuesWire, wire.LinearIssueListWire),
    ),
    WireCase(
        "project",
        "get_project",
        lambda s: {"query": s.project},
        (
            scope_types.LinearApprovalProjectWire,
            scope_types.LinearScopeProjectWire,
            wire.LinearProjectWire,
        ),
    ),
    WireCase(
        "projects",
        "list_projects",
        lambda _: {"includeArchived": True, "limit": PAGE},
        (scope_types.LinearScopeProjectsWire,),
    ),
    WireCase(
        "initiative",
        "get_initiative",
        lambda s: {"query": s.initiative, "includeSubInitiatives": True},
        (
            scope_types.LinearApprovalInitiativeWire,
            scope_types.LinearScopeInitiativeWire,
        ),
    ),
    WireCase(
        "comments",
        "list_comments",
        lambda s: {"issueId": s.root_issue},
        (wire.LinearCommentListWire,),
    ),
)


@pytest.fixture(scope="module")
def deployment() -> AppConfig:
    """The deployment under measurement, or a clean skip: there is none."""
    config = deployment_config()
    if config is None or config.tracker.token is None:
        pytest.skip("no tracker token in the deployment file: nothing to measure")
    return config


@pytest.fixture(scope="module")
def key(deployment: AppConfig) -> str:
    """The credential, judged by the same shape rule boot applies."""
    assert deployment.tracker.token is not None
    value = deployment.tracker.token.get_secret_value()
    refuse_foreign_credential(backend=deployment.tracker.backend, token=value)
    return value


@pytest.fixture(scope="module")
def subjects() -> Subjects:
    """The operator's subjects, or a clean skip naming what is not set."""
    missing = [name for name in SUBJECT_VARIABLES if not os.environ.get(name)]
    if missing:
        pytest.skip(f"nothing to measure: set {', '.join(missing)}")
    project, root_issue, criterion_issue, initiative = (
        os.environ[name] for name in SUBJECT_VARIABLES
    )
    return Subjects(
        project=project,
        root_issue=root_issue,
        criterion_issue=criterion_issue,
        initiative=initiative,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session(
    deployment: AppConfig, key: str
) -> AsyncIterator[ManagedMcpToolCaller]:
    """One session over the transport the deployment dials, closed at the end."""
    caller = make_mcp_tool_caller(settings=deployment.tracker, token=key)
    await caller.probe()
    await caller.open()
    try:
        yield caller
    finally:
        await caller.close()


def _refusals(
    answer: McpToolResult, models: tuple[type[LinearWireModel], ...]
) -> dict[str, str]:
    """Each model's refusal of *answer*, by model name; conformance is absence."""
    refusals: dict[str, str] = {}
    for model in models:
        try:
            model.model_validate(answer)
        except ValidationError as refusal:
            refusals[model.__name__] = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in refusal.errors()
            )
    return refusals


async def _measure(
    session: ManagedMcpToolCaller,
    *,
    label: str,
    tool: str,
    arguments: Mapping[str, object],
    models: tuple[type[LinearWireModel], ...],
) -> McpToolResult:
    """Call once, time it, hold the answer to every model, and record all of it."""
    began = time.monotonic()
    answer = await session.call_tool(name=tool, arguments=arguments)
    seconds = time.monotonic() - began
    refusals = _refusals(answer, models)
    verdicts = "; ".join(
        f"{model.__name__} REFUSED {refusals[model.__name__]}"
        if model.__name__ in refusals
        else f"{model.__name__} ok"
        for model in models
    )
    record(
        probe=PROBE,
        question=f"does {tool} answer in the shape the adapter reads?",
        configuration=f"{label}: {tool} {dict(arguments)}",
        observed=f"{seconds:.2f}s; {verdicts}",
        verdict="conforms" if not refusals else "DRIFT",
    )
    assert not refusals, f"{label}: {tool} drifted from {refusals}"
    return answer


@pytest.mark.parametrize("case", CASES, ids=[case.label for case in CASES])
async def test_the_answer_has_the_shape_the_adapter_reads(
    session: ManagedMcpToolCaller, subjects: Subjects, case: WireCase
) -> None:
    await _measure(
        session,
        label=case.label,
        tool=case.tool,
        arguments=case.arguments(subjects),
        models=case.models,
    )


async def test_a_listed_milestone_reads_back_in_shape(
    session: ManagedMcpToolCaller, subjects: Subjects
) -> None:
    """The one read whose subject comes off the board: the first listed milestone."""
    listing = await _measure(
        session,
        label="milestones",
        tool="list_milestones",
        arguments={"project": subjects.project},
        models=(scope_types.LinearScopeMilestonesWire,),
    )
    milestones = scope_types.LinearScopeMilestonesWire.model_validate(
        listing
    ).milestones
    if not milestones:
        record(
            probe=PROBE,
            question="does get_milestone answer in the shape the adapter reads?",
            configuration=f"project {subjects.project}",
            observed="the project lists no milestone to read",
            verdict="unmeasured",
        )
        return
    await _measure(
        session,
        label="milestone",
        tool="get_milestone",
        arguments={"project": subjects.project, "query": milestones[0].id},
        models=(scope_types.LinearScopeMetadataWire,),
    )


async def test_the_key_has_hourly_request_budget_left(key: str) -> None:
    """The budget a boot is about to spend, read off the vendor's own headers.

    One trivial read.  The reset header is an instant in epoch milliseconds.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            GRAPHQL_URL,
            headers={"Authorization": key},
            json={"query": "{ viewer { id } }"},
        )
    limit = response.headers.get("x-ratelimit-requests-limit")
    remaining = response.headers.get("x-ratelimit-requests-remaining")
    reset = response.headers.get("x-ratelimit-requests-reset")
    if remaining is None or reset is None:
        observed = f"no rate-limit headers on HTTP {response.status_code}"
        verdict = "UNREADABLE"
    else:
        resets_at = datetime.fromtimestamp(int(reset) / 1000, tz=UTC)
        observed = (
            f"{remaining} of {limit} requests "
            f"(~{int(remaining) // REQUESTS_PER_TOOL_CALL} tool calls); "
            f"resets {resets_at:%H:%M:%S} UTC"
        )
        verdict = "available" if int(remaining) > 0 else "SPENT"
    record(
        probe=PROBE,
        question="how much of the key's hourly request budget is left?",
        configuration=f"{GRAPHQL_URL}, HTTP {response.status_code}",
        observed=observed,
        verdict=verdict,
    )
    assert response.status_code == 200, f"HTTP {response.status_code}"
    assert remaining is not None and reset is not None, "no rate-limit headers"
