"""The actual compiled fire excludes every delivery node and route."""

import inspect
from pathlib import Path

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    CriteriaValidation,
    CriterionFeasibility,
    CriterionVerdict,
)
from kodezart.types.domain.delivery import LaneDelivery
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.trajectory import LoopTrajectory
from kodezart.types.domain.workflow import WorkflowState
from tests.chains.test_fire_extraction import DELIVERY_FIELDS, fire

#: Every field the fire's terminal event DECLARES, and every key its state
#: declares, as both stood when the hand-off was pinned.  Closed sets rather
#: than a screen for the five legacy delivery spellings in
#: ``DELIVERY_FIELDS``: "the hand-off adds no field" is a claim about ANY
#: field, and a screen answers only about the names somebody thought to
#: write down — ``delivery_pr_url`` would walk straight past it.  A field the
#: fire genuinely grows is added here in the commit that grows it, which is
#: the review this pin exists to force.
TERMINAL_FIELDS = {
    "accepted",
    "criteria_validation",
    "feature_branch",
    "final_commit_sha",
    "merge_error",
    "merged",
    "outcome",
    "ralph_branch",
    "total_iterations",
    "trajectory",
    "type",
}
STATE_KEYS = {
    "accept_verdict",
    "acceptance_criteria",
    "best_iteration_sha",
    "criteria_infeasible",
    "criteria_regeneration_rounds",
    "criteria_validation",
    "criterion_set",
    "feature_branch",
    "feature_tip_sha",
    "fire_spec",
    "flagged_items",
    "issue_key",
    "lane_entry",
    "merge_error",
    "merged",
    "ralph_branch",
    "remediation_entry",
    "remediation_rounds_used",
    "remediation_ticket",
    "repo_url",
    "repo_visibility",
    "review_base_sha",
    "review_feedback",
    "review_head_sha",
    "review_passed",
    "ruling_unrecorded",
    "total_iterations",
    "trajectory",
    "work_base_ref",
}
#: Every key the terminal SERIALISES: the union over the renderings above and
#: over the instances below, measured at the commit this pin was written.  It
#: is a union rather than one rendering of one instance because a delivery key
#: needs to appear in only ONE of them to reach a consumer — the snake and
#: camel spellings of the same field are both in it for that reason.
TERMINAL_WIRE_KEYS = {
    "accepted",
    "criteriaValidation",
    "criteria_validation",
    "featureBranch",
    "feature_branch",
    "finalCommitSha",
    "final_commit_sha",
    "mergeError",
    "merge_error",
    "merged",
    "outcome",
    "ralphBranch",
    "ralph_branch",
    "totalIterations",
    "total_iterations",
    "trajectory",
    "type",
}
#: What the lane's delivery record and the fire's two surfaces already share,
#: measured at the same commit as the rosters above.  These two names are the
#: fire's own facts that the record repeats back, not delivery facts that
#: migrated in; every other name on ``LaneDelivery`` — the pull request, the
#: observation, the red class, the stall and remediation flags — belongs to
#: the delivery alone, and the empty intersection with the state says so.
SHARED_WITH_TERMINAL = {"final_commit_sha", "outcome"}
SHARED_WITH_STATE: set[str] = set()


def test_compiled_fire_has_no_delivery_nodes_routes_or_capabilities():
    engine = fire()
    graph = engine.graph.get_graph()
    forbidden = {"open_pr", "monitor_ci", "comment_failure", "open_stalled_pr"}
    assert not forbidden.intersection(graph.nodes)
    assert not any(
        edge.source in forbidden or edge.target in forbidden for edge in graph.edges
    )
    assert not DELIVERY_FIELDS.intersection(WorkflowState.__annotations__)
    assert not DELIVERY_FIELDS.intersection(WorkflowCompleteEvent.model_fields)
    parameters = inspect.signature(RalphWorkflowEngine).parameters
    assert "pr_creator" not in parameters and "ci_monitor" not in parameters
    source = Path(inspect.getfile(RalphWorkflowEngine)).read_text()
    for name in (
        "_open_pr_node",
        "_monitor_ci_node",
        "_comment_failure_node",
        "_route_after_pr",
        "_route_after_ci",
    ):
        assert name not in source


def emitted_terminal() -> WorkflowCompleteEvent:
    """One terminal built with the fire's own required facts and nothing else.

    Every optional field keeps its default: the all-default half of the pair
    the roster is measured over.
    """
    return WorkflowCompleteEvent(
        feature_branch="feature/pinned",
        ralph_branch="ralph/pinned",
        total_iterations=1,
        accepted=False,
        outcome=WorkflowOutcome.loop_not_accepted,
    )


def carrying_terminal() -> WorkflowCompleteEvent:
    """The same terminal with every optional field CARRYING a value.

    The other half of the pair, and the half a roster taken from one
    all-default instance cannot answer for: a key that is serialised only
    when its field holds something is absent from the default rendering and
    present here, so a delivery fact reaching a consumer on exactly the runs
    that have one would be invisible to a roster measured once over defaults.
    """
    return WorkflowCompleteEvent(
        feature_branch="feature/pinned",
        ralph_branch="ralph/pinned",
        total_iterations=1,
        accepted=True,
        outcome=WorkflowOutcome.handed_off_for_delivery,
        merged=True,
        final_commit_sha="c" * 40,
        merge_error="a consolidation refusal",
        trajectory=LoopTrajectory(
            records=[],
            never_passed_ids=[],
            best_passed_count=0,
            best_iteration=0,
            plateaued=False,
        ),
        criteria_validation=CriteriaValidation(
            verdicts=[
                CriterionFeasibility(
                    criterion_id="AC-1", verdict=CriterionVerdict.feasible
                )
            ],
            conjunction=ConjunctionVerdict(satisfiable=True),
        ),
    )


def serialised_keys(*events: WorkflowCompleteEvent) -> set[str]:
    """Every key *events* put on a wire, under every rendering they have.

    A key reaches a consumer if ANY rendering carries it, so all four the
    event has are asked and the answers unioned: the python dump; the json
    dump, which a field serialiser of its own can shape differently; the
    ALIASED dump, since every field on this base carries a camelCase alias
    and a field declaring an alias of its own serialises under that name and
    under no other; and the dump that keeps a ``None``, which a key can be
    present in while absent from the default one.
    """
    return {
        key
        for event in events
        for rendering in (
            event.model_dump(),
            event.model_dump(mode="json"),
            event.model_dump(by_alias=True),
            event.model_dump(exclude_none=False),
        )
        for key in rendering
    }


def test_the_fire_terminal_and_state_grow_no_field_of_the_lane_s_delivery():
    """The other half of the hand-off claim: neither surface grew a field.

    ``DELIVERY_FIELDS`` above screens five legacy spellings, so it answers
    about the names it lists and about nothing else.  Two derived pins close
    that: each surface's whole roster, so a field arriving under any spelling
    reds; and the delivery record intersected with each surface, so a
    delivery fact moving onto the fire is named by the intersection it joins
    even if the rosters are updated in the same breath.

    The terminal's roster is the UNION OF WHAT SERIALISES — every key any of
    the event's four renderings carries, over two constructed instances, one
    all-default and one carrying every optional field — rather than what one
    instance happened to carry under one rendering.  A roster read off
    ``model_fields`` is closed against declarations only, and a delivery fact
    grown as a computed field is absent there and present on the wire; a
    roster read off one all-default python-mode dump is closed against that
    one rendering, and a fact that arrives under an alias, under the json
    rendering, or only on the runs that have one is absent there and present
    on the wire as well.  The declaration roster is kept beside it, so a
    field declared but held back from every rendering reds too.
    """
    assert (
        serialised_keys(emitted_terminal(), carrying_terminal()) == TERMINAL_WIRE_KEYS
    )
    assert set(WorkflowCompleteEvent.model_fields) == TERMINAL_FIELDS
    assert set(WorkflowState.__annotations__) == STATE_KEYS

    delivery = set(LaneDelivery.model_fields)
    assert delivery & set(WorkflowCompleteEvent.model_fields) == SHARED_WITH_TERMINAL
    assert delivery & set(WorkflowState.__annotations__) == SHARED_WITH_STATE
