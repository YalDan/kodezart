"""The tracker double is one class per role over one store (KOD-835).

Every class the double is built from answers for exactly one role: its own
body declares exactly the members that role declares, no more and no fewer,
and no role is answered by two classes. The composed double declares no
member of its own, so the conformance suite runs over the roles composed,
unchanged. Each role's class constructs alone and satisfies its role, which
is what lets a test that needs one role build only that role's double.

A role double answers its role plus the roles it composes and the roles
whose members it calls through: its bases are exactly the doubles of the
declaring roles its role composes and of the classes defining what its own
body calls on itself, both derived, so it answers nothing wider and works
when it is built alone. The store it is built over declares nothing public.

The classes are found by what they declare, read off the module's own text,
assignments counted as well as definitions, and the roles by the register
the port guards derive, so a member moved between roles moves this guard
with it. Each live class is then read by object as well: the public names of
its own ``vars()`` are its role's, its MRO holds only the store and role
doubles, nothing forwards a member through ``__getattr__``, and it satisfies
no role beyond what it composes and calls through; the store's ``vars()``
hold nothing public at all.
"""

import ast
import inspect
from pathlib import Path

import pytest

from kodezart.core import protocols
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerComment
from tests import fakes
from tests.domain.test_lane_record import record_data
from tests.fakes import FIXTURE_EPOCH, FakeTrackerPort
from tests.tracker import test_lane_records as lane
from tests.tracker.role_register import (
    class_per_role,
    classes_outside_one_role,
    consumer_classes,
    declared_by_role,
    declaring_roles,
    edge_report,
    implementation_classes,
    members_declared,
    needed_classes,
    port_module_text,
    roles_implemented_nowhere,
    roles_implemented_twice,
)
from tests.tracker.role_register import roles as register_roles

#: The store every role class is built over: the base the composed double's
#: resolution order ends on before ``object``.
STATE = FakeTrackerPort.__mro__[-2]
WHOLE = FakeTrackerPort.__name__
MODULE_TEXT = Path(inspect.getsourcefile(FakeTrackerPort) or "").read_text()


def role_classes(text: str = MODULE_TEXT) -> dict[str, frozenset[str]]:
    return implementation_classes(text, state=STATE.__name__, whole=WHOLE)


def consumer_doubles(text: str = MODULE_TEXT) -> dict[str, str]:
    """The docstring-only doubles, each with the consumer role it composes."""
    return consumer_classes(text, state=STATE.__name__, whole=WHOLE)


def double_per_role() -> dict[str, str]:
    """The one double that answers for each role, declaring or consumer."""
    return {
        **class_per_role(role_classes()),
        **{role: name for name, role in consumer_doubles().items()},
    }


def whole_body(text: str = MODULE_TEXT) -> list[ast.stmt]:
    """The composed double's own body; the last definition is the one bound."""
    *_, node = (
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef) and node.name == WHOLE
    )
    return node.body


def whole_declares(text: str = MODULE_TEXT) -> frozenset[str]:
    """Everything the composed double's own body holds besides its docstring."""
    body = whole_body(text)
    return frozenset(
        ast.unparse(item)
        for item in body
        if not (
            item is body[0]
            and isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        )
    )


def test_every_class_of_the_double_answers_for_exactly_one_role():
    classes = role_classes()
    consumers = consumer_doubles()
    register = port_module_text()

    assert classes_outside_one_role(classes, consumers) == {}
    assert roles_implemented_twice(classes) == {}
    assert roles_implemented_nowhere(classes) == frozenset()
    assert sorted(consumers.values()) == sorted(
        register_roles(register) - declaring_roles(register)
    )


def test_the_composed_double_declares_nothing_and_composes_every_role_class():
    assert whole_declares() == frozenset()
    declaring = set(role_classes()) - set(consumer_doubles())
    assert {cls.__name__ for cls in FakeTrackerPort.__mro__} == declaring | {
        WHOLE,
        STATE.__name__,
        "object",
    }


def test_each_role_double_is_built_over_exactly_the_doubles_it_needs():
    assert edge_report(MODULE_TEXT, state=STATE.__name__, whole=WHOLE) == {}


def test_the_store_declares_no_public_callable():
    """Nothing public at all: a classmethod or a property is no callable."""
    assert [name for name in vars(STATE) if not name.startswith("_")] == []


def live_doubles() -> dict[str, type]:
    """Every class the double's module defines over the store, off the module."""
    return {
        cls.__name__: cls
        for _, cls in inspect.getmembers(fakes, inspect.isclass)
        if cls.__module__ == fakes.__name__
        and issubclass(cls, STATE)
        and cls is not STATE
    }


def test_each_double_s_live_members_are_its_roles():
    """Read off the live class, so a member bound after the body is seen too."""
    role_of = {name: role for role, name in double_per_role().items()}
    declared = declared_by_role()
    doubles = live_doubles()
    wrong = {
        name: sorted(public)
        for name, cls in doubles.items()
        if (public := {member for member in vars(cls) if not member.startswith("_")})
        != (set() if name == WHOLE else set(declared.get(role_of.get(name, ""), ())))
    }

    assert set(doubles) == set(role_of) | {WHOLE}
    assert wrong == {}


def test_every_double_is_built_over_the_store_and_role_doubles_only():
    doubles = live_doubles()
    allowed = {STATE, object, *doubles.values()}

    assert {
        name: [base.__name__ for base in cls.__mro__ if base not in allowed]
        for name, cls in doubles.items()
        if set(cls.__mro__) - allowed
    } == {}


def test_no_double_forwards_members_dynamically():
    assert [
        f"{cls.__name__}.{hook}"
        for cls in (STATE, *live_doubles().values())
        for hook in ("__getattr__", "__getattribute__")
        if hook in vars(cls)
    ] == []


def test_each_double_satisfies_no_role_outside_its_closure():
    """A double answers its role, what it composes and what it calls through."""
    register = port_module_text()
    needs = needed_classes(MODULE_TEXT, state=STATE.__name__, whole=WHOLE)
    role_of = {name: role for role, name in double_per_role().items()}
    declared = declared_by_role()
    every = sorted(register_roles(register))
    outside: dict[str, list[str]] = {}
    for name, cls in live_doubles().items():
        reached, frontier = set(), [name]
        while frontier:
            current = frontier.pop()
            if current not in reached:
                reached.add(current)
                frontier.extend(needs.get(current, ()))
        answered = set().union(
            *(declared.get(role_of.get(held, ""), frozenset()) for held in reached)
        )
        closure = {
            role
            for role in every
            if name == WHOLE or members_declared(register, role) <= answered
        }
        double = cls()
        if found := [
            role
            for role in every
            if isinstance(double, getattr(protocols, role)) and role not in closure
        ]:
            outside[name] = found

    assert outside == {}


@pytest.mark.parametrize("role", sorted(register_roles(port_module_text())))
def test_each_role_class_constructs_alone_and_satisfies_its_role(role):
    double = getattr(fakes, double_per_role()[role])()

    assert isinstance(double, getattr(protocols, role))
    assert isinstance(double, STATE)
    assert not isinstance(double, FakeTrackerPort)


async def test_a_consumer_of_one_role_runs_over_that_role_class_alone():
    """The lane record reader needs comments, so it gets the comment double only."""
    comments = getattr(fakes, class_per_role(role_classes())["TrackerCommentReader"])()
    stored = TrackerComment(
        comment_key="comment-0001",
        issue_key=lane.APPROVED_ISSUE,
        author_key="kodezart",
        body=render_lane_record(
            record=LaneRunState.model_validate(record_data()),
            marker_prefixes=lane.PREFIXES,
        ),
        created_at=FIXTURE_EPOCH,
    )
    comments.comments.append(stored)

    comment, record = await LaneRecordReader(
        tracker=comments, operation=lane.OPERATION
    ).read(**lane.ADDRESS, record_ref=stored.comment_key)

    assert comment == stored
    assert record.lane_key == lane.LANE
    assert not hasattr(comments, "upsert_comment")


def planted(*, members: frozenset[str], name: str, base: str) -> str:
    body = "".join(
        f"    async def {member}(self):\n        ...\n" for member in sorted(members)
    )
    return f"\n\nclass {name}({base}):\n{body or '    ...'}\n"


#: Each way the double could stop being one class per role: the text that
#: arrives, and the one report that must name it.
PLANTED_DOUBLES = {
    "a member outside its role": ("wider", "outside"),
    "a second class for one role": ("second", "twice"),
    "a member on the composed double": ("whole", "whole"),
}


@pytest.mark.parametrize("form", sorted(PLANTED_DOUBLES))
def test_a_double_that_leaves_its_roles_is_reported(form):
    roles = declared_by_role()
    _, members = min(roles.items())
    other = next(iter(sorted(roles[max(roles)])))
    shape, expected = PLANTED_DOUBLES[form]
    text = (
        MODULE_TEXT
        + {
            "wider": planted(
                members=members | {other}, name="WiderDouble", base=STATE.__name__
            ),
            "second": planted(
                members=members, name="SecondDouble", base=STATE.__name__
            ),
            "whole": planted(members=members, name=WHOLE, base=STATE.__name__),
        }[shape]
    )
    classes = role_classes(text)

    reports = {
        "outside": bool(classes_outside_one_role(classes, consumer_doubles(text))),
        "twice": bool(roles_implemented_twice(classes)),
        "whole": bool(whole_declares(text)),
    }

    assert [name for name, reported in reports.items() if reported] == [expected]
