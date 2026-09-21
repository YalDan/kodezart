"""Actual authored consumers retain the dispatch-base prompt bytes."""

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from kodezart.chains import (
    authored_publication,
    fire_implementation,
    remediation,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import ConjunctionVerdict, CriteriaArtifact
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.workflow import ExecutionContext
from tests.chains.test_ralph_workflow import _make_engine
from tests.chains.test_remediation import _chain, _request, _ticket_result
from tests.domain.test_fire_spec_formatter import tickets
from tests.fakes import (
    FakeAgentRunner,
    FakePRCreator,
    FakeQualityGate,
    RecordingPromptProvider,
    make_criteria,
    make_passing_evaluation_of_fake_criteria,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

GOLDENS = Path(__file__).with_name("fire_spec_prompt_goldens.json")
CORPUS = list(tickets())
REPO_ROOT = Path(__file__).resolve().parents[2]
#: This file's path as git knows it, for reads of an earlier state of it.
GOLDENS_IN_TREE = "tests/chains/fire_spec_prompt_goldens.json"


def git_output(*arguments: str) -> str:
    """Read from the history of the tree under test; a failure is a failure."""
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"`git {' '.join(arguments)}` exited {completed.returncode}: "
        f"{completed.stderr.strip()}. This comparison is graded against a base "
        "commit recorded in the goldens file, so a checkout without that "
        "commit's history cannot grade it."
    )
    return completed.stdout


def is_ancestor(commit: str, ref: str) -> bool:
    """Whether *commit* is reachable from *ref* in the tree under test."""
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 and 1 are the two answers; anything else means git could not answer,
    # which is a loud failure and never a quiet False.
    assert completed.returncode in (0, 1), (
        f"`git merge-base --is-ancestor {commit} {ref}` exited "
        f"{completed.returncode}: {completed.stderr.strip()}. A checkout that "
        "cannot resolve the recorded base cannot grade this comparison."
    )
    return completed.returncode == 0


def recorded_base() -> str:
    """The commit this file names as the base of record."""
    return str(json.loads(GOLDENS.read_text())["source_commit"])


def document_at(commit: str) -> dict[str, object]:
    """This file exactly as the named commit carries it."""
    return json.loads(git_output("show", f"{commit}:{GOLDENS_IN_TREE}"))


def flat(entries: Mapping[str, object]) -> dict[str, str]:
    """The nested prompt entries as one mapping, family/index/site -> digest."""
    return {
        f"{family}/{index}/{site}": digest
        for family, cases in entries.items()
        for index, sites in cases.items()
        for site, digest in sites.items()
    }


def kept_entries(
    recorded: Mapping[str, str], current: Mapping[str, str]
) -> dict[str, str | None]:
    """What the current file carries at each recorded key, absent keys as None."""
    return {key: current.get(key) for key in recorded}


def entry_digest(entries: Mapping[str, str | None]) -> str:
    """One digest over the canonical JSON of the entries handed in."""
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def capture_prompts(family, ticket, monkeypatch):
    provider = RecordingPromptProvider(load_registry(default_set=family))
    execution = ExecutionContext(
        prompt="The original dispatched prompt.",
        repo_path="/checkout",
        repo_url="https://github.com/example/project",
        cache_key="original-job",
        base_spec=trunk_base("selected-base"),
        permission_mode=PermissionMode.ACCEPT_EDITS,
        allowed_tools=["Read"],
    )
    quality_gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation_of_fake_criteria(),
    )
    engine = _make_engine(
        quality_gate=quality_gate, pr_creator=FakePRCreator(), prompts=provider
    )
    runner = FakeAgentRunner(
        [
            ResultEvent(
                result="",
                session_id="description",
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                structured_output={"title": "Authored title", "description": "Body."},
            )
        ]
    )
    engine.publication._service = runner
    monkeypatch.setattr(
        fire_implementation, "get_stream_writer", lambda: lambda _: None
    )
    monkeypatch.setattr(
        authored_publication, "get_stream_writer", lambda: lambda _: None
    )
    state = {
        "issue_key": "subject/42",
        # The authored arm enters no lane, which is the value prepare puts
        # here for it; the loop reads it for the head a resumed lane carries.
        "lane_entry": None,
        "fire_spec": AuthoredSpec(ticket=ticket),
        "remediation_ticket": None,
        "feature_branch": "feature",
        "ralph_branch": "loop",
        "work_base_ref": "selected-base",
        "feature_tip_sha": "a" * 40,
        "criterion_set": CriteriaArtifact(
            criteria=make_criteria("Recorded criterion"),
            conjunction=ConjunctionVerdict(satisfiable=True),
        ),
        "total_iterations": 7,
        "repo_visibility": RepoVisibility.PUBLIC,
        "flagged_items": [],
    }
    config = {"configurable": execution.model_dump()}
    await engine.fire.implementation.run_ralph_loop(state, config)
    await engine.publication.open_pr(state, config)

    fix_runner = FakeAgentRunner([_ticket_result()])
    request = _request().model_copy(
        update={"original_spec": AuthoredSpec(ticket=ticket)}
    )
    _ = [
        event
        async for event in _chain(fix_runner, provider).run(
            request, repo_path="/checkout", repo_url=None, cache_key="fix"
        )
    ]
    return {
        "implementation": quality_gate.calls[0]["prompt"],
        "fix": fix_runner.calls[0]["prompt"],
        "workflow_pr": runner.calls[0]["prompt"],
    }


@pytest.mark.parametrize("family", [V5_SET, OPUS_SET])
@pytest.mark.parametrize("index", range(len(CORPUS)))
async def test_real_authored_consumer_prompts_match_recorded_base(
    family, index, monkeypatch
):
    golden = json.loads(GOLDENS.read_text())
    calls = []

    def formatted(spec):
        calls.append(spec)
        return format_fire_spec(spec)

    for module in (
        fire_implementation,
        authored_publication,
        remediation,
    ):
        monkeypatch.setattr(module, "format_fire_spec", formatted)
    actual = await capture_prompts(family, CORPUS[index], monkeypatch)
    assert len(calls) == 3
    assert all(isinstance(spec, AuthoredSpec) for spec in calls)
    assert all(spec.ticket == CORPUS[index] for spec in calls)
    assert {
        name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for name, prompt in actual.items()
    } == golden["prompts"][family][str(index)]


def test_the_base_of_record_is_reachable_from_the_head_under_test() -> None:
    """The named base is an ancestor of the tree under test, not a string."""
    base = recorded_base()
    assert is_ancestor(base, "HEAD"), (
        f"the goldens name {base} as the base of record and it is not an "
        "ancestor of the head under test; a base nobody can check out cannot "
        "hold the recorded bytes (KOD-416)"
    )
    # The reading is directional, so a helper that answers yes to anything
    # fails here: the head is not an ancestor of its own ancestor.
    assert not is_ancestor("HEAD", base)


def test_the_base_document_is_read_out_of_the_history() -> None:
    """The base blob is an earlier state of this file, so it names another base."""
    base = recorded_base()
    assert document_at(base)["source_commit"] != base


async def test_recorded_goldens_are_extended_and_never_rebaselined(
    monkeypatch,
) -> None:
    """No entry the base of record carries moves; new entries may be added."""
    base = recorded_base()
    recorded = flat(document_at(base)["prompts"])
    # The surface the base blob must cover is derived from the prompt sets, the
    # corpus and the site names the capture helper returns -- never hand-listed,
    # so a base whose blob is thinner than the corpus cannot pass as one.
    sites = set(await capture_prompts(V5_SET, CORPUS[0], monkeypatch))
    assert set(recorded) == {
        f"{family}/{index}/{site}"
        for family in (V5_SET, OPUS_SET)
        for index in range(len(CORPUS))
        for site in sites
    }
    current = flat(json.loads(GOLDENS.read_text())["prompts"])
    kept = kept_entries(recorded, current)
    assert entry_digest(kept) == entry_digest(recorded), (
        "entries recorded at the base of record moved: "
        f"{sorted(key for key in recorded if kept[key] != recorded[key])}"
    )


@pytest.mark.parametrize(
    ("current", "moved"),
    [
        ({"a/0/fix": "1", "a/1/fix": "2"}, False),
        ({"a/0/fix": "9", "a/1/fix": "2"}, True),
        ({"a/1/fix": "2", "a/0/fix": "1"}, False),
        ({"a/0/fix": "1", "a/1/fix": "2", "b/0/fix": "3"}, False),
        ({"a/0/fix": "1"}, True),
    ],
)
def test_the_base_comparison_refuses_a_moved_entry_and_allows_a_new_one(
    current, moved
) -> None:
    """Unchanged and extended pass; a moved or deleted recorded entry does not."""
    recorded = {"a/0/fix": "1", "a/1/fix": "2"}
    assert (
        entry_digest(kept_entries(recorded, current)) != entry_digest(recorded)
    ) is moved
