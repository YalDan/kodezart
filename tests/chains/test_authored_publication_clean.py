"""The pull-request node itself is the caller that cleans the artifact branch."""

import uuid

from kodezart.chains import authored_publication
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import ConjunctionVerdict, CriteriaArtifact
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.workflow import ExecutionContext
from tests.chains.test_ralph_workflow import _make_engine
from tests.domain.test_fire_spec_formatter import tickets
from tests.fakes import (
    FakeAgentRunner,
    FakeArtifactPersister,
    FakePRCreator,
    FakeQualityGate,
    make_criteria,
    make_passing_evaluation_of_fake_criteria,
)


async def test_the_pull_request_node_cleans_the_feature_branch_it_is_about_to_open(
    monkeypatch,
) -> None:
    """open_pr is the caller of clean, with the branch and keys it carries."""
    persister = FakeArtifactPersister()
    engine = _make_engine(
        artifact_persister=persister,
        pr_creator=FakePRCreator(),
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation_of_fake_criteria(),
        ),
    )
    engine.publication._service = FakeAgentRunner(
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
    monkeypatch.setattr(
        authored_publication, "get_stream_writer", lambda: lambda _: None
    )
    # Two values no double can be written to hold: the recorded mapping is
    # graded against these locals, so a clean() that appends a fixed mapping
    # whatever it is handed cannot satisfy the equality at the end.
    branch = f"feature-{uuid.uuid4().hex}"
    cache_key = uuid.uuid4().hex
    execution = ExecutionContext(
        prompt="The original dispatched prompt.",
        repo_path="/checkout",
        repo_url="https://github.com/example/project",
        cache_key=cache_key,
        base_spec=trunk_base("selected-base"),
        permission_mode=PermissionMode.ACCEPT_EDITS,
        allowed_tools=["Read"],
    )
    state = {
        "issue_key": "subject/42",
        "lane_entry": None,
        "fire_spec": AuthoredSpec(ticket=next(iter(tickets()))),
        "remediation_ticket": None,
        "feature_branch": branch,
        # The loop branch is the one the node must not clean.
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

    # The node is awaited directly, not reached through an engine run, so the
    # assertion below pins which node calls clean and not merely that some
    # node on the authored path does.
    await engine.publication.open_pr(state, {"configurable": execution.model_dump()})

    assert persister.clean_calls == [
        {
            "repo_path": "/checkout",
            "repo_url": "https://github.com/example/project",
            "branch": branch,
            "cache_key": cache_key,
        }
    ]
