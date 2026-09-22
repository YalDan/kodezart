"""No ticket draft is made anywhere but the two authored drafting sites (KOD-419).

The tracker arm parses no body into a draft because the whole package makes a
draft in exactly two authored places, names the class as a value in two type
tests, and calls a model method on two receivers whose own function states no
type for them.  The register below carries all three kinds and says what each
is, so a third entry in any kind is read rather than absorbed.

The scanned surface is derived, not listed: every module that imports or
declares the class, reaches it through a module it imports, or imports a
carrier whose annotation hands the value around.  The construction forms the
shared walk knows are reported — the class call, the building and parsing
model methods however the receiver is reached, a subclass, an adapter or
partial built around the class, ``type(x)(...)`` and ``x.__class__(...)``.
The adapter and partial forms are caught when the class is handed bare: the
walk reads an argument that spells the class itself, so a class wrapped in a
generic or a union — ``TypeAdapter(list[TicketDraftOutput])``,
``TypeAdapter(TicketDraftOutput | None)``, a subscript and a binary operation
rather than the class — is not seen by this walk.

Stated blind spots.  A construction whose receiver is reached through a string
constant — ``getattr(agent, "TicketDraftOutput")``, a ``model_fields`` lookup
keyed by a word — names the class nowhere this walk reads, because a string
constant is read here only inside an annotation, never as a receiver or a
lookup key.  Less escapes than that sentence alone suggests: in a module the
derived surface already holds, such a receiver handed a model method is still
reported, as one of the unstated-receiver parses below, since silence about a
receiver is not a statement that it is something else.  What goes unreported
is a call that is no model method at all, as in
``AuthoredSpec.model_fields["ticket"].annotation(...)``, and any form at all
in a module the derived surface never reaches.

A draft parsed through a carrier model is the other blind spot:
``AuthoredSpec.model_validate({"ticket": {...}})`` makes the draft in a nested
parse and names its class nowhere, so the register reports it nowhere.  It is
the natural nested route a tracker-arm bypass would take, and what stops it at
head is a side effect rather than this ban, at each of the three places the
arm has a tracker body in hand.  In ``domain/fire_spec.py`` and in
``adapters/linear/tracker.py`` it is the floor below: neither module holds the
draft at head, so an import that reaches a carrier reds the floor's two
statements of that.  Two import routes are counted: the carrier imported by
name, and the module declaring it imported as a module — ``import
kodezart.types.domain.fire_spec as m`` or ``from kodezart.types.domain import
fire_spec`` — whatever the local word.  Three are not, and a carrier reached
through one of them leaves either module off the surface: ``import kodezart``
followed by the whole dotted path, a package's ``__init__`` or a relative
import, and a module that only re-exports the carrier.  In
``chains/criteria.py``, which holds the draft already, the stop is the
arm-reads guard (KOD-410), because there the body is read off a spec.  The
adapter's entry read is the site no second stop covers: what it returns is a
tracker issue rather than a spec, so there is no read of a spec's text for
that guard to see, and the floor line naming the adapter is the whole of what
reds a body parsed into a draft there, by the two counted routes only.
Nothing at head writes either form.  Closing the string-constant one means
running the code rather than reading it; closing the carrier one means
registering construction over every holder class of the draft on the arm,
which needs a decision about the holder construction the arm may legitimately
do (KOD-105).
"""

import pytest

from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.fire_spec import AuthoredSpec
from tests.identity_guards import model_value_sites, value_holders
from tests.name_resolution import source_tree

DRAFT = TicketDraftOutput.__name__
#: The partition's carrier of the draft and the module declaring it, read off
#: the class so a moved or renamed carrier moves the planted routes with it.
CARRIER = AuthoredSpec.__name__
CARRIER_HOME = AuthoredSpec.__module__

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
#: One model-method call on a receiver whose own function states no type for it
#: (a comprehension variable).  The shared walk reports it because silence about
#: a receiver is not a statement that it is something else; it was read and makes
#: no draft.  The classmethod that used to sit beside it, the run context's parse
#: from its configurable, states its own receiver now (KOD-695), so the walk no
#: longer has to guess and the register no longer carries it.
UNSTATED_RECEIVERS = {
    "build": ("chains/lane_delivery.py::__init__",),
    "parse": (),
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
#: The adapter no longer composes the spec — KOD-710 moved that to the stage that
#: is its one caller — so the point where the arm holds a tracker body is the
#: subject it returns from the entry read.
TRACKER_ANCHOR = "        return subject\n"
TRACKER_MODULE = "adapters/linear/tracker.py"

#: Where a tracker body is in hand, the name holding it, and the function the
#: report would name.  Each anchor is asserted unique before it is replaced.
PLANT_SITES = (
    (
        "domain/fire_spec.py",
        "    return TrackerSpec(\n",
        "subject",
        "tracker_spec_from_issues",
    ),
    (TRACKER_MODULE, TRACKER_ANCHOR, "subject", "read_fire_subject"),
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
    and names no draft — and neither is the adapter the subject is read
    through, so the reddening plants below import the class, which is the
    natural form a bypass would take.  Those two assertions are statements of
    head rather than bans: any carrier or class import into either module reds
    them, and their message is what tells a reader who adds a legitimate one
    that the surface moved.  The adapter's does more than that, being the one
    thing that reds a tracker body parsed into a draft through a carrier at
    the arm's own entry read, by the two import routes the module docstring
    counts.
    """
    holders = value_holders(source_tree(), identity=DRAFT)

    assert holders.keys() >= TRACKER_ARM_MODULES
    assert "domain/fire_spec.py" not in holders
    assert TRACKER_MODULE not in holders


#: Each spelling of the module route to the carrier: the module bound under an
#: alias, a submodule bound by a ``from`` import, and the bare dotted import.
CARRIER_PACKAGE, _, CARRIER_LEAF = CARRIER_HOME.rpartition(".")
CARRIER_ROUTES = {
    "aliased_module": (f"import {CARRIER_HOME} as routed\n", f"routed.{CARRIER}"),
    "submodule_from_its_package": (
        f"from {CARRIER_PACKAGE} import {CARRIER_LEAF}\n",
        f"{CARRIER_LEAF}.{CARRIER}",
    ),
    "dotted_module": (f"import {CARRIER_HOME}\n", f"{CARRIER_HOME}.{CARRIER}"),
}


@pytest.mark.parametrize("route", sorted(CARRIER_ROUTES))
def test_a_carrier_reached_through_its_module_puts_the_adapter_on_the_surface(
    route,
):
    """The adapter's floor line reds a carrier parse however its module is bound.

    The nested parse names no draft, so the floor is what stops it at the
    entry read, and the floor rested on the carrier's own name: binding the
    carrier's module instead and reading the carrier off it walked past.  Each
    route plants the parse of the subject's body into the carrier before the
    entry read returns, and the adapter must join the scanned surface.
    """
    sources = source_tree()
    assert sources[TRACKER_MODULE].count(TRACKER_ANCHOR) == 1
    imports, carrier = CARRIER_ROUTES[route]
    sources[TRACKER_MODULE] = imports + sources[TRACKER_MODULE].replace(
        TRACKER_ANCHOR,
        f"        draft = {carrier}.model_validate("
        "{'ticket': {'title': subject.body}})\n" + TRACKER_ANCHOR,
    )

    assert TRACKER_MODULE in value_holders(sources, identity=DRAFT)


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
