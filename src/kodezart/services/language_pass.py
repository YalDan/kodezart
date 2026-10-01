"""The language pass: what an iteration wrote, read by a cheap session.

The loop knows nothing about the words a session chose; it knows which
branches moved while the session ran, because the cache's clone heads are
read before the session and again after it. Each moved branch is diffed
against its trunk and the patches go to one short question on the cheap
engine (``PromptKey.LANGUAGE_PASS``), whose answer is a list of findings
the grader is handed. The principle lives in that prompt; this module holds
the arithmetic around it and no opinion about words.
"""

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    RepoCache,
)
from kodezart.services.agent_question import ask
from kodezart.types.domain.agent import LanguageFinding, LanguagePassOutput
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SkillsSelection

_log: BoundLogger = get_logger(__name__)

#: How much of one branch's patch the question is shown. Identifiers,
#: comments and docs sit in the first lines of a change far more often than
#: not, and a cheap session has a short context; the cut is stated in the
#: patch text so the reader knows it saw a prefix.
PATCH_BYTES_PER_BRANCH = 200_000


class BranchChange(NamedTuple):
    """One branch an iteration moved, with the patch it added over its trunk."""

    repository: str
    branch: str
    trunk: str
    head_sha: str
    patch: str


async def snapshot_heads(
    *,
    git: GitService,
    cache: RepoCache,
    repositories: Sequence[RepoEntry],
    cache_key: str | None,
) -> dict[str, dict[str, str]]:
    """Every declared repository's branch heads, by repository url."""
    heads: dict[str, dict[str, str]] = {}
    for repository in repositories:
        clone = await cache.ensure_available(repository.url, cache_key)
        heads[repository.url] = await git.branch_heads(clone)
    return heads


async def moved_branches(
    *,
    git: GitService,
    cache: RepoCache,
    repositories: Sequence[RepoEntry],
    before: Mapping[str, Mapping[str, str]],
    cache_key: str | None,
) -> list[BranchChange]:
    """The branches whose head is new or moved since *before*, with their patches.

    A branch equal to its trunk's head added nothing and is left out; the
    trunk itself is left out too, because the loop never writes it.
    """
    changes: list[BranchChange] = []
    for repository in repositories:
        clone = await cache.ensure_available(repository.url, cache_key)
        now = await git.branch_heads(clone)
        earlier = before.get(repository.url, {})
        for branch, head_sha in sorted(now.items()):
            if branch == repository.trunk or earlier.get(branch) == head_sha:
                continue
            patch = await git.diff_patch(
                clone, repository.trunk, branch, PATCH_BYTES_PER_BRANCH
            )
            if not patch:
                continue
            changes.append(
                BranchChange(repository.url, branch, repository.trunk, head_sha, patch)
            )
    return changes


def change_variables(changes: Sequence[BranchChange]) -> dict[str, object]:
    """The render variables the language-pass prompt reads."""
    return {
        "changes": [
            {
                "repository": change.repository,
                "branch": change.branch,
                "trunk": change.trunk,
                "head_sha": change.head_sha,
                "patch": change.patch,
            }
            for change in changes
        ],
    }


async def language_findings(
    *,
    runner: AgentRunner,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    workspace_path: str,
    changes: Sequence[BranchChange],
) -> list[LanguageFinding] | None:
    """Ask the language question over *changes*: the findings, or ``None``.

    No change means nothing to read and an empty answer without a session.
    ``None`` means the question went unanswered; the caller decides what
    that is worth (the grader is told the pass did not run).
    """
    if not changes:
        return []
    answer = await ask(
        runner=runner,
        prompts=prompts,
        skills=skills,
        workspace_path=workspace_path,
        key=PromptKey.LANGUAGE_PASS,
        bindings=change_variables(changes),
        answer=LanguagePassOutput,
    )
    if answer is None:
        return None
    await _log.ainfo(
        "language_pass_answered",
        branches=[f"{c.repository}#{c.branch}" for c in changes],
        findings=len(answer.findings),
    )
    return list(answer.findings)


def finding_variables(findings: Sequence[LanguageFinding] | None) -> dict[str, object]:
    """What the grader's and the implementer's prompts read about the words.

    Presence is the renderer's conditional: ``language_findings`` is bound
    only when there is at least one; ``language_pass_unanswered`` only when
    the question was asked and not answered.
    """
    if findings is None:
        return {"language_pass_unanswered": True}
    if not findings:
        return {}
    return {
        "language_findings": [
            {
                "location": finding.location,
                "phrase": finding.phrase,
                "standard_term": finding.standard_term,
                "why": finding.why,
            }
            for finding in findings
        ],
    }
