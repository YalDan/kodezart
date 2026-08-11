"""Live probes for KOD-53/AC-13 and KOD-53/AC-23.

The fire-level ``[decision]`` of 2026-08-11 supersedes the standing
"NOT SATISFIED at every sha this branch reaches" pin for exactly these
two criteria: each closes via a live-marked probe, EXCLUDED from CI,
that runs the real generation path once on a fixture input and asserts
the property of the ACTUAL generated criteria.  The rendered-prompt
substitution stays forbidden (KOD-36 R3): nothing below reads prompt
text as evidence of compliance — every assertion is over what the model
generated.

The dispatch mirrors ``_generate_criteria_node`` exactly: the
composition-root prompt registry renders ``ACCEPTANCE_CRITERIA`` with
the node's variables, ``AgentService.stream`` carries it with
``EVAL_PERMISSION_MODE`` / ``EVAL_TOOLS_WITH_AGENT`` and the
``GENERATED_CRITERIA_SCHEMA`` structured-output contract, and the
result is validated and minted as the node validates and mints it.
Skills are suppressed so the probe observes the prompt set alone —
the same posture as the empty-skills golden family.

These tests call the REAL Claude Agent SDK (no mocks).
Skip by default; run with: ``pytest -m live``.
"""

import asyncio
import re
from pathlib import Path

import pytest

from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS_WITH_AGENT
from kodezart.core.stream_drain import drain
from kodezart.domain.criteria import mint_criteria
from kodezart.services.agent_service import AgentService
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
    repo: Path,
    base_ref: str,
    tmp_path: Path,
) -> list[GeneratedCriterion]:
    """One real generator call — the node's dispatch, minus the graph."""
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
    service = AgentService(executor=executor, workspace=workspace)

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


_CLAUSE_BOUNDARY = re.compile(r"[.;:!?]+(?=\s|$)")
_INLINE_CODE = re.compile(r"`[^`]+`")

_NEGATED = re.compile(
    r"\b(?:no|not|never|without|nor|none|nothing|neither|cannot|zero)\b|n't\b"
)
_ABBREVIATION = re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.|cf\.|vs\.)", re.IGNORECASE)
_PUNISHED = re.compile(
    r"\b(?:is|are)\s+(?:an?\s+)?(?:hard\s+)?"
    r"(?:fail|failure|violation|regression|defect)s?\b"
    r"|\bfails\b"
    r"|\bforbid(?:s|den)?\b"
)
_PUNISHED_INVERSION = re.compile(
    r"\b(?:absence|absent|missing|omitted|omitting|omission|lacking|lacks|unless)\b"
)
_PRESERVED = re.compile(
    r"\b(?:still|unchanged|byte-identical|remains?|stays?|kept|preserved|untouched)\b"
)


def _clauses(text: str) -> list[str]:
    """Sentence-level clauses, with inline code opaque to the splitter.

    Punctuation inside an inline-code span or an abbreviation ("e.g.",
    "i.e.") is not a clause boundary — the dot in
    ``RunOutcome.rolled_back`` and the colon in ``case ...:`` never end
    a clause — and outside those a boundary needs trailing whitespace,
    so an attribute dot never severs a token from the clause that
    governs it.
    """
    masked = _INLINE_CODE.sub(
        lambda match: re.sub(r"[.;:!?]", " ", match.group(0)),
        text,
    )
    masked = _ABBREVIATION.sub(
        lambda match: match.group(0).replace(".", " "),
        masked,
    )
    return [clause for clause in _CLAUSE_BOUNDARY.split(masked) if clause.strip()]


def _guarded(clause: str) -> bool:
    """Whether *clause* guards against its token rather than demanding it.

    Three guard families, each judged over the whole governing clause:

    - **negated** — the clause denies the token's presence ("contains no
      occurrence of", "does NOT gain a case for", "zero matches");
    - **preserved** — the clause pins existing state ("stays
      byte-identical", "still carrying ... for wire compatibility"); the
      probe's premise pins the completion surfaces free of the token, so
      preserving what exists cannot demand adding it;
    - **punished** — the clause deems the token's presence a failure ("a
      ``case ...:`` arm is a hard fail", "fails the gate", "forbidden"),
      unless it punishes the token's ABSENCE ("absence of the arm is a
      hard fail"), which is a demand and stays one.
    """
    lowered = clause.lower()
    if _NEGATED.search(lowered) or _PRESERVED.search(lowered):
        return True
    return bool(
        _PUNISHED.search(lowered) and not _PUNISHED_INVERSION.search(lowered)
    )


def _demands(text: str, token: str) -> bool:
    """Whether *text* demands *token* rather than guarding against it.

    A criterion that GUARDS against the token asserts the property the
    probe checks and is never an offender; the token is judged against
    its governing clause, never against a fragment severed at an
    attribute dot or inside an inline-code span.  The default is
    conservative — a clause containing the token is a demand unless a
    guard family exculpates it — and the full transcript printed above
    is the evidence of record for the verdict either way.
    """
    return any(
        token in clause and not _guarded(clause) for clause in _clauses(text)
    )


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


async def test_live_generated_criteria_complete_behaviorally_not_literally(
    tmp_path: Path,
) -> None:
    """KOD-53/AC-13 live probe, per the 2026-08-11 decision on the fire.

    The fixture repo declares ``RunOutcome`` with three arms and a switch
    discriminating only two; a sibling type and a routes document both
    name a ``rolled_back`` route the type does not declare, and the
    ticket does not imply it.  Behavioral-over-literal completion is then
    two observable properties of the generated criteria:

    - the declared-but-undiscriminated arm (``plateaued``) is demanded —
      completion enumerated from the type's ACTUAL definition;
    - no criterion DEMANDS the arm the type does not declare
      (``rolled_back``) — an arm inferred from a sibling type or from
      what the type "ought" to have never reaches the criteria.  A
      criterion forbidding the phantom arm asserts the same property the
      probe checks and is not an offender.
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

    criteria = await _generate_live(
        PATTERN_5_LIVE_TICKET,
        repo=repo,
        base_ref="main",
        tmp_path=tmp_path,
    )

    texts = [criterion.text for criterion in criteria]
    joined = " ".join(texts)
    assert "plateaued" in joined, (
        "behavioral completion must reach the declared arm the switch "
        f"misses; generated criteria: {texts}"
    )
    offenders = [text for text in texts if _demands(text, "rolled_back")]
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


async def test_live_scope_criteria_state_the_resolved_stacked_base(
    tmp_path: Path,
) -> None:
    """KOD-53/AC-23 live probe, per the 2026-08-11 decision on the fire.

    The run is fired against a stacked base — a blocker branch carrying
    inherited work — and the ticket's no-touch clause invites scope
    criteria.  The generated criteria must contain the resolved base
    ref, and no criterion may invoke ``git diff`` without stating that
    base in its own text: a bare diff, or a diff against a base other
    than the resolved one, leaves the lane convicted of the work it
    inherited.
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

    criteria = await _generate_live(
        STACKED_LIVE_TICKET,
        repo=repo,
        base_ref=STACKED_BASE_REF,
        tmp_path=tmp_path,
    )

    texts = [criterion.text for criterion in criteria]
    joined = " ".join(texts)
    assert STACKED_BASE_REF in joined, (
        "generated criteria never state the resolved base ref; "
        f"generated criteria: {texts}"
    )
    bare_diffs = [
        text
        for text in texts
        if STACKED_BASE_REF not in text and _demands(text, "git diff")
    ]
    assert bare_diffs == [], (
        "a criterion invokes git diff without stating the resolved base "
        f"in its own text: {bare_diffs}"
    )
