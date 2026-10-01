"""The language pass: what an iteration wrote, read by a cheap session.

The loop knows nothing about the words a session chose; it knows which
branches moved while the session ran, because the cache's clone heads are
read before the session and again after it. Each moved branch is read
against its base (the branch its open pull request targets, or the trunk
when it has none): the patch, the commit messages and the pull request's
prose go to one short question on the cheap engine
(``PromptKey.LANGUAGE_PASS``), whose answer is a list of findings the grader
is handed. The principle lives in that prompt; this module holds the
arithmetic around it and no opinion about words.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    PullRequestTextReader,
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

#: How much of one branch's commit messages the question is shown.
MESSAGE_BYTES_PER_BRANCH = 20_000


class BranchChange(NamedTuple):
    """One branch an iteration moved: what it added over its base, in words."""

    repository: str
    branch: str
    base: str
    head_sha: str
    patch: str
    commit_messages: str
    pull_request_text: str


class LanguagePass:
    """Reads the words each iteration wrote and asks the language question.

    Holds no run state: the repositories, the heads before the session and
    the workspace arrive per call, so one object serves every loop the
    composition builds. ``pull_requests_for`` gives the open-pull-request
    reader for one repository url, chosen by origin at the composition
    root; where it gives none, or none is supplied, a branch is read
    against its trunk and with no pull-request text.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        git: GitService,
        cache: RepoCache,
        pull_requests_for: Callable[[str], PullRequestTextReader | None] | None = None,
    ) -> None:
        self._runner = runner
        self._prompts = prompts
        self._skills = skills
        self._git = git
        self._cache = cache
        self._pull_requests_for = pull_requests_for

    async def snapshot(
        self, *, repositories: Sequence[RepoEntry], cache_key: str | None
    ) -> dict[str, dict[str, str]]:
        """Every declared repository's branch heads, by repository url."""
        heads: dict[str, dict[str, str]] = {}
        for repository in repositories:
            clone = await self._cache.ensure_available(repository.url, cache_key)
            heads[repository.url] = await self._git.branch_heads(clone)
        return heads

    async def moved(
        self,
        *,
        repositories: Sequence[RepoEntry],
        before: Mapping[str, Mapping[str, str]],
        cache_key: str | None,
    ) -> list[BranchChange]:
        """The branches whose head is new or moved since *before*, read whole.

        A branch equal to its base added nothing and is left out; the trunk
        itself is left out too, because the loop never writes it.
        """
        changes: list[BranchChange] = []
        for repository in repositories:
            clone = await self._cache.ensure_available(repository.url, cache_key)
            now = await self._git.branch_heads(clone)
            earlier = before.get(repository.url, {})
            for branch, head_sha in sorted(now.items()):
                if branch == repository.trunk or earlier.get(branch) == head_sha:
                    continue
                change = await self._read(
                    cwd=clone,
                    repository=repository.url,
                    branch=branch,
                    trunk=repository.trunk,
                    head=head_sha,
                )
                if change is not None:
                    changes.append(change)
        return changes

    async def own(
        self, *, cwd: str, repository: str, branch: str, base: str, head: str
    ) -> BranchChange | None:
        """A per-issue run's own branch over the base the run was given."""
        return await self._read(
            cwd=cwd,
            repository=repository,
            branch=branch,
            trunk=base,
            head=head,
            look_up_pull_request=False,
        )

    async def findings(
        self, *, workspace_path: str, changes: Sequence[BranchChange]
    ) -> list[LanguageFinding] | None:
        """Ask the language question over *changes*: the findings, or ``None``.

        No change means nothing to read and an empty answer without a
        session. ``None`` means the question went unanswered; the caller
        decides what that is worth (the grader is told the pass did not run).
        """
        if not changes:
            return []
        answer = await ask(
            runner=self._runner,
            prompts=self._prompts,
            skills=self._skills,
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

    async def _read(
        self,
        *,
        cwd: str,
        repository: str,
        branch: str,
        trunk: str,
        head: str,
        look_up_pull_request: bool = True,
    ) -> BranchChange | None:
        """One branch over its base, or ``None`` when it added nothing."""
        reader = (
            self._pull_requests_for(repository)
            if look_up_pull_request and self._pull_requests_for is not None
            else None
        )
        opened = (
            await reader.open_pr_text(repo_url=repository, head=branch)
            if reader is not None
            else None
        )
        base = trunk if opened is None else opened.base_branch
        patch = await self._git.diff_patch(cwd, base, head, PATCH_BYTES_PER_BRANCH)
        if not patch:
            return None
        messages = await self._git.commit_messages(
            cwd, base, head, MESSAGE_BYTES_PER_BRANCH
        )
        text = "" if opened is None else f"{opened.title}\n\n{opened.body}".strip()
        return BranchChange(repository, branch, base, head, patch, messages, text)


def change_variables(changes: Sequence[BranchChange]) -> dict[str, object]:
    """The render variables the language-pass prompt reads."""
    rendered: list[dict[str, str]] = []
    for change in changes:
        entry = {
            "repository": change.repository,
            "branch": change.branch,
            "base": change.base,
            "head_sha": change.head_sha,
            "patch": change.patch,
            "commit_messages": change.commit_messages,
        }
        if change.pull_request_text:
            entry["pull_request_text"] = change.pull_request_text
        rendered.append(entry)
    return {"changes": rendered}


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
