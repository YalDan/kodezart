"""No ticket draft is made anywhere but the two authored drafting sites (KOD-419).

The tracker arm parses no body into a draft because the whole package makes a
draft in exactly two authored places, names the class as a value in two type
tests, and calls a model method on two receivers whose own function states no
type for them.  The register below carries all three kinds and says what each
is, so a third entry in any kind is read rather than absorbed.

The scanned surface is derived, not listed: every module that imports or
declares the class, reaches it through a module it imports, or imports a
carrier whose annotation hands the value around.  Every construction form the
shared walk knows is reported — the class call, the building and parsing
model methods however the receiver is reached, a subclass, an adapter or
partial built around the class, ``type(x)(...)`` and ``x.__class__(...)``.

Stated blind spot: a construction that names the class only inside a string
constant, such as ``getattr(module, "TicketDraftOutput")`` or a
``model_fields`` lookup keyed by a word, or that never names it at all, as in
``AuthoredSpec.model_fields["ticket"].annotation(...)``, is invisible to this
walk and to any other static one: a string constant is read here only inside
an annotation, never as a receiver or a lookup key.  Nothing at head does it,
and a reader who wants it closed has to run the code rather than read it.
"""

import pytest

from kodezart.types.domain.agent import TicketDraftOutput
from tests.identity_guards import model_value_sites, value_holders
from tests.name_resolution import source_tree

DRAFT = TicketDraftOutput.__name__

#: The two authored drafting sites: the only places the package makes a draft.
CONSTRUCTIONS = {
    "parse": (
        "chains/remediation.py::run",
        "chains/ticket_generation.py::_create_node",
    )
}
#: Two positions where the class is the second argument of isinstance; the
#: shared walk counts a call handed the class as a value use.  A third entry
#: here is a reading of the site, never a construction.
TYPE_TESTS = {
    "build": (
        "chains/fire_remediation.py::draft_remediation",
        "domain/workflow_state.py::current_fire_spec",
    )
}
#: Two model-method calls on a receiver whose own function states no type for
#: it (a comprehension variable, a classmethod's cls).  The shared walk
#: reports them because silence about a receiver is not a statement that it
#: is something else; each was read and makes no draft.
UNSTATED_RECEIVERS = {
    "build": ("chains/lane_delivery.py::__init__",),
    "parse": ("types/domain/workflow.py::from_configurable",),
}

REGISTER = {
    "build": tuple(sorted(TYPE_TESTS["build"] + UNSTATED_RECEIVERS["build"])),
    "parse": tuple(sorted(CONSTRUCTIONS["parse"] + UNSTATED_RECEIVERS["parse"])),
}

#: The tracker-arm modules that turn issue text into a captured subject.  A
#: floor rather than the whole surface, because the carrier fixed point grows
#: with every module that hands a workflow state around.
TRACKER_ARM_MODULES = frozenset(
    {
        "types/domain/fire_spec.py",
        "domain/ticket.py",
        "domain/workflow_state.py",
        "chains/remediation.py",
        "chains/ticket_generation.py",
        "chains/fire_remediation.py",
        "chains/criteria.py",
    }
)

PLAIN_IMPORT = f"from kodezart.types.domain.agent import {DRAFT}\n"
MODULE_IMPORT = "import kodezart.types.domain.agent as agent\n"
ALIASED_IMPORT = f"from kodezart.types.domain.agent import {DRAFT} as Draft\n"

CRITERIA_ANCHOR = (
    "    require_unamended_subject("
    'issue_key=issue_key, entry=state["lane_entry"], spec=spec)\n'
)
TRACKER_ANCHOR = (
    "        return tracker_spec_from_issues(subject=subject, criteria=criteria)\n"
)

#: Where a tracker body is in hand, the name holding it, and the function the
#: report would name.  Each anchor is asserted unique before it is replaced.
PLANT_SITES = (
    (
        "domain/fire_spec.py",
        "    return TrackerSpec(\n",
        "subject",
        "tracker_spec_from_issues",
    ),
    ("adapters/linear/tracker.py", TRACKER_ANCHOR, "subject", "read_fire_spec"),
    ("chains/criteria.py", CRITERIA_ANCHOR, "spec", "revalidate_criteria"),
)

FORMS = (
    "model_validate",
    "model_validate_json",
    "model_construct",
    "direct_call",
    "module_route",
    "aliased_import",
)


def planted_draft(form, receiver):
    """The import a form needs, and the expression that makes a draft."""
    body = f"{receiver}.body"
    if form == "model_validate":
        return PLAIN_IMPORT, f"{DRAFT}.model_validate({{'title': {body}}})"
    if form == "model_validate_json":
        return PLAIN_IMPORT, f"{DRAFT}.model_validate_json({body})"
    if form == "model_construct":
        return PLAIN_IMPORT, f"{DRAFT}.model_construct(title={body})"
    if form == "direct_call":
        return PLAIN_IMPORT, f"{DRAFT}(title={body})"
    if form == "module_route":
        return MODULE_IMPORT, f"agent.{DRAFT}.model_validate({{'title': {body}}})"
    return ALIASED_IMPORT, f"Draft.model_validate({{'title': {body}}})"


def test_the_draft_is_made_only_where_the_authored_arm_drafts_one():
    """Every value use of the draft in the package, compared to the register.

    The two authored sites both parse a session's structured output: the
    remediation chain's authored branch, and the ticket generation node the
    native graph never compiles.  The other four are readings of the class,
    not makings of one, and the register says which is which.
    """
    assert model_value_sites(source_tree(), identity=DRAFT) == REGISTER


def test_the_tracker_arm_modules_are_on_the_scanned_surface():
    """The ban is not vacuous: the arm's own modules are scanned.

    ``domain/fire_spec.py`` is not a holder at head — it captures a subject
    and names no draft — so the reddening plant below imports the class,
    which is the natural form a bypass would take.
    """
    holders = value_holders(source_tree(), identity=DRAFT)

    assert holders.keys() >= TRACKER_ARM_MODULES
    assert "domain/fire_spec.py" not in holders


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize(
    ("module", "anchor", "receiver", "function"),
    PLANT_SITES,
    ids=[module for module, _anchor, _receiver, _function in PLANT_SITES],
)
def test_the_ban_reddens_when_the_tracker_arm_parses_a_body_into_a_draft(
    module, anchor, receiver, function, form
):
    """A draft made from a tracker body on the arm is reported, in every form.

    The first row is the mutation the criterion was refuted on: a
    ``model_validate`` of the subject's own body inside the tracker spec
    capture.  The rest are the other forms the same bypass can take,
    including the class reached through the module it lives in and under an
    import alias.
    """
    sources = source_tree()
    assert sources[module].count(anchor) == 1
    imports, expression = planted_draft(form, receiver)
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    sources[module] = imports + sources[module].replace(
        anchor, f"{indent}draft = {expression}\n{anchor}"
    )

    sites = model_value_sites(sources, identity=DRAFT)

    assert f"{module}::{function}" in sites["build"] + sites["parse"]
    assert sites != REGISTER
