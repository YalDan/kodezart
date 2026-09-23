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
with it.
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
    declared_by_role,
    edge_report,
    implementation_classes,
    roles_implemented_nowhere,
    roles_implemented_twice,
)

#: The store every role class is built over: the base the composed double's
#: resolution order ends on before ``object``.
STATE = FakeTrackerPort.__mro__[-2]
WHOLE = FakeTrackerPort.__name__
MODULE_TEXT = Path(inspect.getsourcefile(FakeTrackerPort) or "").read_text()


def role_classes(text: str = MODULE_TEXT) -> dict[str, frozenset[str]]:
    return implementation_classes(text, state=STATE.__name__, whole=WHOLE)


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

    assert classes_outside_one_role(classes) == {}
    assert roles_implemented_twice(classes) == {}
    assert roles_implemented_nowhere(classes) == frozenset()


def test_the_composed_double_declares_nothing_and_composes_every_role_class():
    assert whole_declares() == frozenset()
    assert {cls.__name__ for cls in FakeTrackerPort.__mro__} == set(role_classes()) | {
        WHOLE,
        STATE.__name__,
        "object",
    }


def test_each_role_double_is_built_over_exactly_the_doubles_it_needs():
    assert edge_report(MODULE_TEXT, state=STATE.__name__, whole=WHOLE) == {}


def test_the_store_declares_no_public_callable():
    held = {
        name: value for name, value in vars(STATE).items() if not name.startswith("_")
    }

    assert [name for name, value in held.items() if callable(value)] == []


@pytest.mark.parametrize("role", sorted(declared_by_role()))
def test_each_role_class_constructs_alone_and_satisfies_its_role(role):
    double = getattr(fakes, class_per_role(role_classes())[role])()

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
        "outside": bool(classes_outside_one_role(classes)),
        "twice": bool(roles_implemented_twice(classes)),
        "whole": bool(whole_declares(text)),
    }

    assert [name for name, reported in reports.items() if reported] == [expected]
