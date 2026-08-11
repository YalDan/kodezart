"""Live probes for KOD-53/AC-13 and KOD-53/AC-23.

The fire-level ``[decision]`` of 2026-08-11 supersedes the standing
"NOT SATISFIED at every sha this branch reaches" pin for exactly these
two criteria: each closes via a live-marked probe, EXCLUDED from CI,
that runs the real generation path once on a fixture input and asserts
the property of the ACTUAL generated criteria.  The rendered-prompt
substitution stays forbidden (KOD-36 R3): nothing below reads prompt
text as evidence of compliance — every assertion is over what the model
generated.

The generation dispatch mirrors ``_generate_criteria_node`` exactly: the
composition-root prompt registry renders ``ACCEPTANCE_CRITERIA`` with
the node's variables, ``AgentService.stream`` carries it with
``EVAL_PERMISSION_MODE`` / ``EVAL_TOOLS_WITH_AGENT`` and the
``GENERATED_CRITERIA_SCHEMA`` structured-output contract, and the
result is validated and minted as the node validates and mints it.
Skills are suppressed so the probe observes the prompt set alone —
the same posture as the empty-skills golden family.

**Compliance is judged by a dispatched call, never by a prose parser.**
The second ``[decision]`` of 2026-08-11 rules the demand/guard
discrimination to be judgment, and it is made by ONE structured-output
dispatch per probe (R9 fixes its shape): a fresh session, no tools, the
generated criteria supplied id-keyed, one verdict per id carrying a span
quoted from that criterion's own text.  The report is reconciled
fail-closed against the dispatched ids — the ``reconcile`` discipline of
:mod:`kodezart.domain.criteria_feasibility` — so a missing, duplicated or
invented id fails the probe rather than shrinking the denominator.

What stays harness-side is only arithmetic: the fixture premises, and
exact-token containment over the generated set.  Nothing here classifies
a sentence.

These tests call the REAL Claude Agent SDK (no mocks).
Skip by default; run with: ``pytest -m live``.
"""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import ConfigDict, Field

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS_WITH_AGENT
from kodezart.core.stream_drain import drain
from kodezart.domain.criteria import mint_criteria
from kodezart.services.agent_service import AgentService
from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import (
    GENERATED_CRITERIA_SCHEMA,
    GeneratedCriteriaOutput,
)
from kodezart.types.domain.criteria import GeneratedCriterion
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.test_lane_verification import default_registry

pytestmark = pytest.mark.live


async def _git(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = f"{' '.join(cmd)} failed: {stderr.decode()}"
        raise RuntimeError(msg)


async def _commit_file(repo: Path, name: str, body: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    await _git(["git", "add", name], cwd=repo)
    await _git(["git", "commit", "-m", message], cwd=repo)


async def _init_repo(repo: Path) -> None:
    repo.mkdir()
    await _git(["git", "init", "-b", "main"], cwd=repo)
    await _git(["git", "config", "user.email", "probe@kodezart-test.invalid"], cwd=repo)
    await _git(["git", "config", "user.name", "kodezart-live-probe"], cwd=repo)


def _probe_service(tmp_path: Path) -> AgentService:
    """The composition root's own wiring for a real dispatch, minus the graph."""
    config = AppConfig()
    git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=git, base_dir=str(tmp_path / "cache"))
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name="kodezart-live-probe",
        committer_email="probe@kodezart-test.invalid",
    )
    executor = ClaudeClientExecutor(
        model=config.model,
        setting_sources=config.setting_sources,
    )
    return AgentService(executor=executor, workspace=workspace)


def _render_generation_prompt(ticket: str, base_ref: str) -> str:
    """The drafter prompt exactly as ``_generate_criteria_node`` renders it."""
    return (
        default_registry()
        .template_for(PromptKey.ACCEPTANCE_CRITERIA)
        .render(
            {
                "task_description": ticket,
                "validation_findings": None,
                "base_ref": base_ref,
            },
        )
    )


async def _generate_live(
    ticket: str,
    *,
    service: AgentService,
    repo: Path,
    base_ref: str,
) -> list[GeneratedCriterion]:
    """One real generator call — the node's dispatch, minus the graph."""
    result_event, rate_limit_rejected = await drain(
        service.stream(
            prompt=_render_generation_prompt(ticket, base_ref),
            repo_path=str(repo),
            branch=base_ref,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=EVAL_TOOLS_WITH_AGENT,
            skills=SkillsSelection(mode=SkillsMode.NONE),
            output_format={
                "type": "json_schema",
                "schema": GENERATED_CRITERIA_SCHEMA,
            },
        )
    )

    assert not rate_limit_rejected, "probe run was rate-limit rejected; not evidence"
    assert result_event is not None
    assert result_event.structured_output is not None
    output = GeneratedCriteriaOutput.model_validate(result_event.structured_output)
    criteria = list(mint_criteria(output.criteria))
    # The probe transcript of record: every generated criterion, in full,
    # so the recorded verdict is re-derivable from the run's own output.
    for criterion in criteria:
        print(f"{criterion.id} [{criterion.criterion_class.value}] {criterion.text}")
    return criteria


# ---------------------------------------------------------------------------
# The judge — one dispatched structured-output call per probe (KOD-53 R9)
# ---------------------------------------------------------------------------


class JudgeVerdict(CamelCaseModel):
    """One criterion judged, with the span the judgment rests on.

    ``evidence`` is required in both directions: a guard verdict that
    cannot quote the guarding clause is not an observation, and the
    recorded verdict must be re-derivable from the transcript.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: str = Field(min_length=1)
    demands: bool
    evidence: str = Field(min_length=1)


class JudgeReport(CamelCaseModel):
    """The judge's verdicts over one dispatched criteria set."""

    verdicts: list[JudgeVerdict] = Field(min_length=1)


JUDGE_SCHEMA: dict[str, object] = JudgeReport.model_json_schema()

JUDGE_PROMPT = """You are judging acceptance criteria that another agent \
generated. Judge ONLY the criterion text supplied below. You have no tools; \
do not infer anything from a repository.

The question, asked separately of every criterion:

{question}

How to answer:
- Return one verdict for EVERY criterion id listed below, exactly once each. \
Never invent an id, never omit one, never answer one twice.
- `demands` is true only when THAT criterion's own text demands the thing the \
question names. A criterion that forbids it, that asserts its absence, that \
punishes its presence, or that merely requires an existing artifact which \
mentions it to stay unchanged, does NOT demand it — answer false.
- `evidence` is a span quoted from that criterion's own text: the clause that \
demands, when true; the clause that guards, forbids or preserves, when false. \
Never empty.

Criteria:

{criteria}
"""


def _judge_lines(criteria: Sequence[GeneratedCriterion]) -> str:
    return "\n\n".join(
        f"{criterion.id} [{criterion.criterion_class.value}]: {criterion.text}"
        for criterion in criteria
    )


def _reconciled(
    criteria: Sequence[GeneratedCriterion],
    report: JudgeReport,
) -> dict[str, JudgeVerdict]:
    """Pair verdicts to dispatched ids 1:1 — ``reconcile``'s discipline.

    Fail-closed on every correspondence hole: an id with no verdict, an id
    answered twice, or a verdict for an id nobody dispatched. A judge that
    answers eleven of twelve licenses no verdict over the eleven.
    """
    dispatched = [criterion.id for criterion in criteria]
    dispatched_set = set(dispatched)
    seen: dict[str, JudgeVerdict] = {}
    duplicates: list[str] = []
    unknown: list[str] = []
    for verdict in report.verdicts:
        if verdict.criterion_id not in dispatched_set:
            unknown.append(verdict.criterion_id)
        elif verdict.criterion_id in seen:
            duplicates.append(verdict.criterion_id)
        else:
            seen[verdict.criterion_id] = verdict
    missing = [id_ for id_ in dispatched if id_ not in seen]
    assert not (missing or duplicates or unknown), (
        "the judge's report does not correspond 1:1 to the dispatched "
        f"criteria (missing: {missing}; duplicate: {duplicates}; "
        f"unknown: {unknown})"
    )
    return seen


async def _judge_live(
    criteria: Sequence[GeneratedCriterion],
    *,
    question: str,
    service: AgentService,
    repo: Path,
    base_ref: str,
) -> dict[str, JudgeVerdict]:
    """One dispatched judgment over the generated criteria, reconciled.

    A fresh session — no ``session_id`` is carried from the generation
    call — and no tools, so the judge answers from the criterion text it
    was handed and from nothing else.
    """
    result_event, rate_limit_rejected = await drain(
        service.stream(
            prompt=JUDGE_PROMPT.format(
                question=question,
                criteria=_judge_lines(criteria),
            ),
            repo_path=str(repo),
            branch=base_ref,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=[],
            skills=SkillsSelection(mode=SkillsMode.NONE),
            output_format={
                "type": "json_schema",
                "schema": JUDGE_SCHEMA,
            },
        )
    )

    assert not rate_limit_rejected, "judge dispatch was rate-limit rejected"
    assert result_event is not None
    assert result_event.structured_output is not None
    judged = _reconciled(
        criteria,
        JudgeReport.model_validate(result_event.structured_output),
    )
    for criterion in criteria:
        verdict = judged[criterion.id]
        print(f"judge {criterion.id}: demands={verdict.demands} :: {verdict.evidence}")
    return judged


def _demand_verdicts(judged: Mapping[str, JudgeVerdict]) -> list[str]:
    """Every id the judge found demanding the forbidden thing, with its span."""
    return [
        f"{criterion_id}: {verdict.evidence}"
        for criterion_id, verdict in judged.items()
        if verdict.demands
    ]


# ---------------------------------------------------------------------------
# KOD-53/AC-13 — behavioral-over-literal completion (Pattern 5), observed on
# the criteria a real generator call yields.
# ---------------------------------------------------------------------------

OUTCOME_MODULE = '''"""Terminal-disposition partition of a pipeline run."""

from enum import StrEnum


class RunOutcome(StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    plateaued = "plateaued"
'''

RENDER_MODULE = '''"""Human-readable rendering of terminal outcomes."""

from outcome import RunOutcome


def render_outcome(outcome: RunOutcome) -> str:
    match outcome:
        case RunOutcome.succeeded:
            return "run succeeded"
        case RunOutcome.failed:
            return "run failed"
    return "unknown outcome"
'''

# The Pattern-5 inducement: a decoy roster of "terminal routes" the type
# does not declare, exactly the "arm you inferred from a sibling type, or
# from what the type 'ought' to have" the prompt's section 7 forbids.
LEGACY_MODULE = '''"""Superseded outcome partition, kept for wire compatibility."""

from enum import StrEnum


class LegacyRunOutcome(StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    plateaued = "plateaued"
    rolled_back = "rolled_back"
'''

ROUTES_DOC = (
    "# Terminal routes\n\n"
    "A run ends on one of the terminal routes: succeeded, failed, "
    "plateaued, rolled_back.\n"
)

PATTERN_5_LIVE_TICKET = (
    "Complete the switch in `render.py` over `RunOutcome` so no terminal "
    "route is left undiscriminated."
)

PHANTOM_ARM_QUESTION = (
    "Does this criterion DEMAND that the implementation handle, add, render "
    "or discriminate a `rolled_back` terminal outcome?\n\n"
    "The situation the criteria were generated over: the type under change, "
    "`RunOutcome`, declares exactly three members — `succeeded`, `failed`, "
    "`plateaued`. A separate superseded type `LegacyRunOutcome` and a "
    "`ROUTES.md` document both mention a `rolled_back` route; the type under "
    "change does not declare it.\n\n"
    "Answer true only if the criterion requires `rolled_back` to be handled, "
    "added, rendered or discriminated by the implementation. Answer false if "
    "the criterion forbids it, requires its absence, requires that the "
    "superseded type or the document keep it unchanged, or does not raise it "
    "at all."
)


async def test_live_generated_criteria_complete_behaviorally_not_literally(
    tmp_path: Path,
) -> None:
    """KOD-53/AC-13 live probe, per the 2026-08-11 decisions on the fire.

    The fixture repo declares ``RunOutcome`` with three arms and a switch
    discriminating only two; a sibling type and a routes document both
    name a ``rolled_back`` route the type does not declare, and the
    ticket does not imply it.  Behavioral-over-literal completion is then
    two observable properties of the generated criteria:

    - the declared-but-undiscriminated arm (``plateaued``) is demanded —
      completion enumerated from the type's ACTUAL definition;
    - no criterion DEMANDS the arm the type does not declare
      (``rolled_back``) — an arm inferred from a sibling type or from
      what the type "ought" to have never reaches the criteria.  Whether
      a criterion demands that arm or guards against it is the judge's
      call, dispatched once over the whole generated set.
    """
    # Premise, pinned against the fixture the probe dispatches: the arm
    # the decoys name is NOT declared by the type, and one declared arm
    # is undiscriminated by the switch.
    assert "rolled_back" not in OUTCOME_MODULE
    assert "rolled_back" not in RENDER_MODULE
    assert "plateaued" in OUTCOME_MODULE
    assert "plateaued" not in RENDER_MODULE
    assert "rolled_back" in LEGACY_MODULE
    assert "rolled_back" in ROUTES_DOC

    repo = tmp_path / "repo"
    await _init_repo(repo)
    await _commit_file(repo, "outcome.py", OUTCOME_MODULE, "feat: outcome partition")
    await _commit_file(repo, "render.py", RENDER_MODULE, "feat: outcome renderer")
    await _commit_file(repo, "legacy_outcome.py", LEGACY_MODULE, "chore: legacy enum")
    await _commit_file(repo, "ROUTES.md", ROUTES_DOC, "docs: terminal routes")

    service = _probe_service(tmp_path)
    criteria = await _generate_live(
        PATTERN_5_LIVE_TICKET,
        service=service,
        repo=repo,
        base_ref="main",
    )

    joined = " ".join(criterion.text for criterion in criteria)
    assert "plateaued" in joined, (
        "behavioral completion must reach the declared arm the switch "
        f"misses; generated criteria: {[c.text for c in criteria]}"
    )

    judged = await _judge_live(
        criteria,
        question=PHANTOM_ARM_QUESTION,
        service=service,
        repo=repo,
        base_ref="main",
    )
    offenders = _demand_verdicts(judged)
    assert offenders == [], (
        "a criterion demands an arm the type does not declare — the "
        f"Pattern-5 literal completion: {offenders}"
    )


# ---------------------------------------------------------------------------
# KOD-53/AC-23 — generated scope criteria state the resolved base, observed
# on the criteria a real generator call yields for a stacked lane.
# ---------------------------------------------------------------------------

STACKED_BASE_REF = "kodezart/blocker-a-live0001"

API_MODULE = '"""Fixture API package."""\n'

INHERITED_MODULE = "def landed_on_the_blocker() -> int:\n    return 1\n"

CORE_MODULE = "def unrelated() -> int:\n    return 0\n"

STACKED_LIVE_TICKET = (
    "Add a `ping` handler in `app/api/handlers.py` returning the literal "
    'string "pong", and export it from `app/api/__init__.py`. Nothing '
    "outside `app/api/` may change."
)

BARE_DIFF_QUESTION = (
    "Does this criterion call for a diff — `git diff`, a changed-file "
    "comparison, a scope check against a baseline — WITHOUT naming "
    f"`{STACKED_BASE_REF}` as the base of THAT comparison?\n\n"
    "The situation the criteria were generated over: the lane's resolved "
    f"comparison base is the branch `{STACKED_BASE_REF}`, which already "
    "carries inherited work. A diff taken against anything else, or against "
    "an unstated baseline, convicts the lane of work it inherited.\n\n"
    "Answer true if the criterion invokes any such comparison and that "
    "comparison's base is unstated, or is stated as something other than "
    f"`{STACKED_BASE_REF}`. The base must be named for the comparison "
    "itself: a mention of the base elsewhere in the criterion, for an "
    "unrelated purpose, does not settle it. Answer false if the criterion "
    "invokes no comparison at all, or if every comparison it invokes names "
    "that base as its own baseline."
)


async def test_live_scope_criteria_state_the_resolved_stacked_base(
    tmp_path: Path,
) -> None:
    """KOD-53/AC-23 live probe, per the 2026-08-11 decisions on the fire.

    The run is fired against a stacked base — a blocker branch carrying
    inherited work — and the ticket's no-touch clause invites scope
    criteria.  The generated criteria must contain the resolved base
    ref, and no criterion may invoke a diff without stating that base as
    that diff's own baseline: a bare diff, or a diff against a base other
    than the resolved one, leaves the lane convicted of the work it
    inherited.  The per-criterion half is the judge's call — a base ref
    named anywhere in a criterion no longer exculpates a bare invocation
    elsewhere in it.
    """
    repo = tmp_path / "repo"
    await _init_repo(repo)
    await _commit_file(repo, "app/api/__init__.py", API_MODULE, "feat: api package")
    await _commit_file(repo, "core/util.py", CORE_MODULE, "feat: core util")
    await _git(["git", "checkout", "-b", STACKED_BASE_REF, "main"], cwd=repo)
    await _commit_file(
        repo,
        "app/api/inherited.py",
        INHERITED_MODULE,
        "feat: work landed on the blocker",
    )
    await _git(["git", "checkout", "main"], cwd=repo)

    service = _probe_service(tmp_path)
    criteria = await _generate_live(
        STACKED_LIVE_TICKET,
        service=service,
        repo=repo,
        base_ref=STACKED_BASE_REF,
    )

    joined = " ".join(criterion.text for criterion in criteria)
    assert STACKED_BASE_REF in joined, (
        "generated criteria never state the resolved base ref; "
        f"generated criteria: {[c.text for c in criteria]}"
    )

    judged = await _judge_live(
        criteria,
        question=BARE_DIFF_QUESTION,
        service=service,
        repo=repo,
        base_ref=STACKED_BASE_REF,
    )
    bare_diffs = _demand_verdicts(judged)
    assert bare_diffs == [], (
        "a criterion invokes a diff without stating the resolved base "
        f"as its baseline: {bare_diffs}"
    )
