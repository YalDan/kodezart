"""The Linear adapter is one class per role over one shared session (KOD-837).

Every class the adapter is built from answers for exactly one role: its own
body declares exactly the members that role declares, and no role is
answered by two classes. None of them constructs anything: the constructor,
the caller, the configured vocabularies and every helper that calls no
public member belong to the one session class, which declares no public
member and sits once at the root of the composed adapter. So one adapter
object is one session, holding the one caller it was given.

How Linear is called is pinned beside this, by the recorded call log in
``test_linear_call_log.py``; this module pins the shape.
"""

import ast
import inspect
from pathlib import Path

import pytest

from kodezart.adapters.linear.tracker import LinearMcpTracker
from tests.fakes import FakeLinearMcpServer
from tests.tracker.role_register import (
    class_per_role,
    classes_outside_one_role,
    declared_by_role,
    implementation_classes,
    roles_implemented_nowhere,
    roles_implemented_twice,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over

#: The shared session: the base the composed adapter's resolution order ends
#: on before ``object``.
SESSION = LinearMcpTracker.__mro__[-2]
WHOLE = LinearMcpTracker.__name__
MODULE_TEXT = Path(inspect.getsourcefile(LinearMcpTracker) or "").read_text()


def class_bodies(text: str) -> dict[str, list[ast.stmt]]:
    """Each class of *text* by name; a name bound twice keeps its last body."""
    return {
        node.name: node.body
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef)
    }


def declared(body: list[ast.stmt]) -> frozenset[str]:
    return frozenset(
        item.name
        for item in body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def public(names: frozenset[str]) -> frozenset[str]:
    return frozenset(name for name in names if not name.startswith("_"))


def role_classes(text: str = MODULE_TEXT) -> dict[str, frozenset[str]]:
    return implementation_classes(text, state=SESSION.__name__, whole=WHOLE)


def constructing_role_classes(text: str = MODULE_TEXT) -> tuple[str, ...]:
    """Every role class that defines a constructor of its own."""
    bodies = class_bodies(text)
    return tuple(
        sorted(
            name for name in role_classes(text) if "__init__" in declared(bodies[name])
        )
    )


def session_public(text: str = MODULE_TEXT) -> frozenset[str]:
    return public(declared(class_bodies(text)[SESSION.__name__]))


def whole_declares(text: str = MODULE_TEXT) -> frozenset[str]:
    return public(declared(class_bodies(text)[WHOLE]))


def test_every_class_of_the_adapter_answers_for_exactly_one_role():
    classes = role_classes()

    assert classes_outside_one_role(classes) == {}
    assert roles_implemented_twice(classes) == {}
    assert roles_implemented_nowhere(classes) == frozenset()
    assert len(classes) == len(declared_by_role())


def test_only_the_session_constructs_and_it_declares_no_member():
    assert constructing_role_classes() == ()
    assert "__init__" in declared(class_bodies(MODULE_TEXT)[SESSION.__name__])
    assert session_public() == frozenset()
    assert whole_declares() == frozenset()


def test_the_composed_adapter_holds_every_role_class_over_one_session():
    resolution = [cls.__name__ for cls in LinearMcpTracker.__mro__]

    assert set(role_classes()) <= set(resolution)
    assert resolution.count(SESSION.__name__) == 1
    assert all(issubclass(cls, SESSION) for cls in LinearMcpTracker.__mro__[:-2])


def test_one_adapter_object_holds_the_one_caller_it_was_given():
    caller = FakeLinearMcpServer()
    adapter = tracker_over(caller)

    held = [value for value in vars(adapter).values() if value is caller]

    assert adapter._caller is caller
    assert len(held) == 1


def planted(*, name: str, base: str, members: frozenset[str]) -> str:
    body = "".join(
        f"    def {member}(self):\n        ...\n" for member in sorted(members)
    )
    return f"\n\nclass {name}({base}):\n{body or '    ...'}\n"


#: Each way the adapter could stop being one class per role over one
#: session: the text that arrives, and the one report that must name it. A
#: class planted under a name the module already binds replaces it, the way
#: a redefinition would.
PLANTED_ADAPTERS = {
    "a member outside its role": ("wider", "outside"),
    "a second class for one role": ("second", "twice"),
    "a role class that constructs": ("constructs", "constructs"),
    "a public member on the session": ("session", "session"),
}


@pytest.mark.parametrize("form", sorted(PLANTED_ADAPTERS))
def test_an_adapter_that_leaves_its_roles_is_reported(form):
    roles = declared_by_role()
    role, members = min(roles.items())
    other = next(iter(sorted(roles[max(roles)])))
    shape, expected = PLANTED_ADAPTERS[form]
    session_body = class_bodies(MODULE_TEXT)[SESSION.__name__]
    session_text = ast.unparse(
        ast.ClassDef(
            name=SESSION.__name__,
            bases=[],
            keywords=[],
            body=[
                *session_body,
                ast.parse(f"def {other}(self):\n    ...\n").body[0],
            ],
            decorator_list=[],
            type_params=[],
        )
    )
    text = (
        MODULE_TEXT
        + {
            "wider": planted(
                name="WiderRole", base=SESSION.__name__, members=members | {other}
            ),
            "second": planted(
                name="SecondRole", base=SESSION.__name__, members=members
            ),
            "constructs": planted(
                name=class_per_role(role_classes())[role],
                base=SESSION.__name__,
                members=members | {"__init__"},
            ),
            "session": f"\n\n{session_text}\n",
        }[shape]
    )
    classes = role_classes(text)

    reports = {
        "outside": bool(classes_outside_one_role(classes)),
        "twice": bool(roles_implemented_twice(classes)),
        "constructs": bool(constructing_role_classes(text)),
        "session": bool(session_public(text)),
    }

    assert [name for name, reported in reports.items() if reported] == [expected]
