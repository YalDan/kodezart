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
