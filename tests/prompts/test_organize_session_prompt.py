"""What the organize session's prompt tells each phase, in every shipped set.

Rendered through the owner's own ``render``, so the per-call bindings are the
ones a live session is handed, against the shipped scope operation file, so
the labels named are the ones a scope deployment declares.
"""

import re

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.organize_session_owner import OrganizeSessionOwner
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner, FakeTrackerPort
from tests.integration.test_scope_deployment import SCOPE_EXAMPLE
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.prompts.test_set_completeness import shipped_sets

SETS = shipped_sets()
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="golden-project")
OWED = ("external/42", "external/43")


def rendered(set_name: str, kind: MandateKind, *, scope: ScopeRef = SCOPE) -> str:
    """One phase's prompt, exactly as the owner renders it for the session."""
    operation = load_operation_config(SCOPE_EXAMPLE)
    rows = operation.resolve_organize_mandates()
    owner = OrganizeSessionOwner(
        members=FakeTrackerPort(),
        approvals=FakeTrackerPort(),
        runner=FakeAgentRunner(events=[]),
        prompts=load_registry(
            default_set=set_name, bindings=operation_bindings(operation)
        ),
        skills=SUPPRESS_ALL_SKILLS,
        phases=rows,
        tracker_server_name="linear",
        working_dir="/tmp/kodezart-organize-session-prompt",
    )
    phase = next(row for row in rows if row.spec.kind is kind)
    return owner.render(phase=phase, scope=scope, owed=OWED)


def sentences(text: str) -> list[str]:
    """Every sentence of *text*, whitespace-normalised."""
    return [part for part in re.split(r"(?<=\.)\s+", " ".join(text.split())) if part]


#: The criterion's three written fields, named together in one sentence.
SHAPE = re.compile(r"\bCheck\b.*\bDo\b.*\bEvidence\b")

#: How each set tells the ticket phase to treat a member's body: filled in
#: where it falls short, inside the sections and shape it already has.
LANE_BODY = {
    V5_SET: (
        "The body keeps its own sections and shape: where it falls short, add"
        " what is missing inside that shape, and leave what already holds."
    ),
    OPUS_SET: (
        "Keep the body's own sections and shape: where it falls short, add what"
        " is missing inside that shape, and keep what already holds."
    ),
}

#: Whose shape Check / Do / Evidence is, in every set.
CRITERION_SHAPE = (
    "Check / Do / Evidence is the shape of a criterion sub-issue, never of a"
    " member's body."
)


@pytest.mark.parametrize("set_name", SETS)
def test_the_ticket_phase_fills_a_body_in_its_own_shape(set_name: str) -> None:
    """A lane's body keeps its sections; only a criterion is Check / Do / Evidence.

    The scratch lanes carry bodies of their own shape, and a ticket phase told
    to give every body the criterion's three fields would rewrite each one into
    a shape the lane was never written in. So every sentence of the ticket
    phase that names the three fields together names a criterion sub-issue as
    the thing that has them, and at least one sentence does.
    """
    text = rendered(set_name, MandateKind.TICKET)
    said = sentences(text)

    assert LANE_BODY[set_name] in said
    assert CRITERION_SHAPE in said
    shaped = [sentence for sentence in said if SHAPE.search(sentence)]
    assert shaped
    unowned = [sentence for sentence in shaped if "criterion sub-issue" not in sentence]
    assert unowned == []
    assert "{{" not in text


#: How each set tells the groom phase to repair condition 4: the one criterion
#: write outside the criteria phase, in the shape and state the criteria phase
#: itself writes. ``{criterion}`` is the operation's criterion label.
MISSING_CRITERION = {
    V5_SET: (
        "Where one is missing, creating it is the repair: a criterion sub-issue"
        " under the member, labelled `{criterion}`, in the team's unstarted"
        " workflow state, with a Check / Do / Evidence body (a Check that can be"
        " shown true or false, a Do that says the work, and an Evidence row left"
        " empty for the graded commit)."
    ),
    OPUS_SET: (
        "Where one is missing, creating it is the repair: a criterion sub-issue"
        " under the member, labelled `{criterion}`, in the team's unstarted"
        " workflow state, with a Check / Do / Evidence body (a Check that can be"
        " shown true or false, a Do that states the work, and an Evidence row left"
        " empty for the graded commit)."
    ),
}

#: What the groom phase may do to a criterion sub-issue, and what every other
#: phase outside the criteria phase may do: one rule, one sentence each.
GROOM_CRITERION_RULE = (
    "Outside the criteria phase, never touch a criterion sub-issue beyond"
    " creating a missing one as condition 4 says."
)
TICKET_CRITERION_RULE = "Outside the criteria phase, never touch a criterion sub-issue."


@pytest.mark.parametrize("set_name", SETS)
def test_the_groom_phase_may_create_a_missing_criterion_and_nothing_more(
    set_name: str,
) -> None:
    """Condition 4 and the criterion rule say one thing.

    Condition 4 asks every executed member to carry a criterion sub-issue, and
    the rule after it used to forbid the groom phase to touch one, so a member
    missing its criterion could be neither repaired nor groomed. The groom
    phase is now told to create the missing one, labelled with the operation's
    criterion label, in the team's unstarted state, with a Check / Do / Evidence
    body, and the one sentence that forbids touching a criterion names that
    act as its only exception. The ticket phase is given no such exception.
    """
    criterion = load_operation_config(SCOPE_EXAMPLE).issue_labels["criterion"]
    groom = sentences(rendered(set_name, MandateKind.GROOM))

    assert MISSING_CRITERION[set_name].format(criterion=criterion) in groom
    forbidding = [s for s in groom if "never touch a criterion sub-issue" in s]
    assert forbidding == [GROOM_CRITERION_RULE]

    ticket = sentences(rendered(set_name, MandateKind.TICKET))
    forbidding = [s for s in ticket if "never touch a criterion sub-issue" in s]
    assert forbidding == [TICKET_CRITERION_RULE]
    assert [s for s in ticket if "creating it is the repair" in s] == []
