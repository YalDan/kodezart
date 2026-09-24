"""What the organize session's prompt tells each phase, in every shipped set.

Rendered through the owner's own ``render``, so the per-call bindings are the
ones a live session is handed, against the shipped scope operation file, so
the labels named are the ones a scope deployment declares.
"""

import re

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.organize import is_organize_subject, owes_stage_label
from kodezart.services.organize_session_owner import OrganizeSessionOwner
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeTrackerPort,
    make_tracker_issue,
)
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


#: What every phase outside the criteria phase may do to a criterion
#: sub-issue: one rule, one sentence, no exception.
TICKET_CRITERION_RULE = "Outside the criteria phase, never touch a criterion sub-issue."


@pytest.mark.parametrize("set_name", SETS)
def test_the_ticket_phase_never_touches_a_criterion_sub_issue(
    set_name: str,
) -> None:
    """The one sentence that forbids touching a criterion carries no exception.

    The retired groom phase was the one phase allowed to create a missing
    criterion; that repair is the grooming pass's now, over the whole board
    before approval. Inside the run, only the criteria phase writes a
    criterion sub-issue, so the ticket phase is told never to touch one and
    is given no repair to make.
    """
    ticket = sentences(rendered(set_name, MandateKind.TICKET))
    forbidding = [s for s in ticket if "never touch a criterion sub-issue" in s]
    assert forbidding == [TICKET_CRITERION_RULE]
    assert [s for s in ticket if "creating it is the repair" in s] == []
    assert [s for s in ticket if "groom" in s.lower()] == []


#: How each set accounts for a member the list leaves out: marked already,
#: owing nothing, or escalated and waiting on a person.
UNLISTED = {
    V5_SET: (
        "A member not listed above already carries the marker, owes nothing to"
        " this phase, or is escalated and is left alone: it is not touched."
    ),
    OPUS_SET: (
        "Members not listed already carry the marker, owe nothing to this phase,"
        " or are escalated and are left alone; you do not touch them."
    ),
}


@pytest.mark.parametrize("set_name", SETS)
def test_a_member_left_off_the_list_may_be_an_escalated_one(set_name: str) -> None:
    """The list leaves out an escalated member that still owes the marker.

    The owner lists only work subjects, and an escalated member is none: it
    carries the decision label, so it is never listed, while it still owes the
    phase's marker and the halt names it. A prompt saying every unlisted member
    either carries the marker or owes nothing would misstate that member, so
    each phase names the third case and leaves it alone.
    """
    escalated = make_tracker_issue("external/44", issue_labels=frozenset({"decision"}))
    assert not is_organize_subject(escalated)
    assert owes_stage_label(escalated)

    for kind in MandateKind:
        assert UNLISTED[set_name] in sentences(rendered(set_name, kind)), kind


#: How each set's one scope-label sentence opens.
SCOPE_LABEL_RULE = "Never add or remove any scope label"


@pytest.mark.parametrize("set_name", SETS)
def test_every_declared_scope_label_is_named_only_to_be_forbidden(
    set_name: str,
) -> None:
    """Triage, proposed and approved alike: the session sets none of them.

    The rule used to name two of the three scope labels, which left the
    proposed one, and any other the operation declares, outside it. Every
    label the shipped scope file declares is read off the file, and in every
    phase the one sentence that names any of them is the rule that forbids
    adding or removing it.
    """
    declared = load_operation_config(SCOPE_EXAMPLE).scope_labels
    assert {member.value for member in ScopeLabel} <= set(declared)

    for kind in MandateKind:
        said = sentences(rendered(set_name, kind))
        naming = [s for s in said if any(label in s for label in declared.values())]
        assert len(naming) == 1, (kind, naming)
        (rule,) = naming
        assert rule.startswith(SCOPE_LABEL_RULE), (kind, rule)
        for label in declared.values():
            assert f"`{label}`" in rule, (kind, label)


#: What the scope's key is to the tracker, by the kind of scope: a container
#: by its id, an issue by its key. ``{key}`` is the scope's own key.
SCOPE_ADDRESS = {
    ScopeKind.PROJECT: "the tracker project whose id is `{key}`",
    ScopeKind.ISSUE: "the tracker issue whose key is `{key}`",
    ScopeKind.INITIATIVE: "the tracker initiative whose id is `{key}`",
    ScopeKind.MILESTONE: "the tracker milestone whose id is `{key}`",
}


@pytest.mark.parametrize("kind", list(ScopeKind), ids=str)
@pytest.mark.parametrize("set_name", SETS)
def test_the_scope_is_named_by_what_its_key_is_to_the_tracker(
    set_name: str, kind: ScopeKind
) -> None:
    """A project scope's key is the project's id; an issue scope's is its key.

    The prompt used to say "the project `<key>`", which leaves the session to
    guess whether the key is an id, a display identifier or a name. Every kind
    of scope an operation can address is rendered, and the opening line names
    its key the way the tracker does, and no other kind's way.
    """
    scope = ScopeRef(kind=kind, key="scope-key-0042")
    opening = rendered(set_name, MandateKind.TICKET, scope=scope).splitlines()[0]

    assert SCOPE_ADDRESS[kind].format(key=scope.key) in opening
    others = [
        SCOPE_ADDRESS[other].format(key=scope.key)
        for other in ScopeKind
        if other is not kind
    ]
    assert [address for address in others if address in opening] == []
