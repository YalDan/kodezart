"""The Linear adapter is one class per role over one shared session (KOD-837).

Every class the adapter is built from answers for exactly one role: its own
body declares exactly the members that role declares, and no role is
answered by two classes. None of them constructs anything: the constructor,
the caller, the configured vocabularies and every helper that calls no
public member belong to the one session class, which declares no public
member and sits once at the root of the composed adapter. So one adapter
object is one session, holding the one caller it was given. That is read by
object too: no class the adapter package builds over the session, but the
session, defines ``_send`` or ``_call`` or has code reading ``self._caller``;
after the scripted run the adapter still holds that one caller and no
other, through every container it keeps; and a role class built alone
outside the adapters is found whatever name it is imported or bound under.

Each role class also answers its whole role: it is built over the role
classes of the declaring roles its role composes and of the classes whose
members its own body reaches, derived as the double's are, so it answers
every member of its role on its own.

How Linear is called is pinned beside this, by the per-case call log of the
conformance suite recorded before the split (``conformance_call_log.json``)
and by the scripted call log in ``test_linear_call_log.py``; this module pins
the shape.
"""

import ast
import copy
import inspect
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import CodeType, FunctionType, ModuleType

import pytest

from kodezart.adapters.linear.tracker import LinearMcpTracker
from kodezart.core.protocols import McpToolCaller
from tests.domain.test_criterion_cross_off import source_tree
from tests.fakes import FakeLinearMcpServer
from tests.tracker.role_register import (
    ADAPTERS,
    UNBOUND,
    adapter_package_modules,
    bound_object,
    class_per_role,
    classes_defined_in,
    classes_outside_one_role,
    declared_by_role,
    edge_report,
    final_name,
    implementation_classes,
    members_declared,
    namespace,
    nodes,
    port_module_text,
    roles_implemented_nowhere,
    roles_implemented_twice,
)
from tests.tracker.test_linear_call_log import STEPS, run, scripted_adapter
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


def whole_holds(text: str = MODULE_TEXT) -> list[str]:
    """Everything the composed adapter's own body holds besides its docstring."""
    body = class_bodies(text)[WHOLE]
    return [
        ast.unparse(item)
        for item in body
        if not (
            item is body[0]
            and isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        )
    ]


def role_class_constructions(sources: dict[str, str]) -> list[str]:
    """Every call outside the adapters that builds an adapter role class.

    The callee is resolved by object in the calling module's namespace, so
    a role class imported under another name, reached through a module
    alias or bound to an alias is the class it is; a name nothing binds is
    read as spelled.
    """
    module = inspect.getmodule(LinearMcpTracker)
    built = {getattr(module, name): name for name in role_classes()}
    found: list[str] = []
    for path, text in sources.items():
        if path.startswith(f"{ADAPTERS}/"):
            continue
        scope = namespace(path, text)
        for node in nodes(text):
            if not isinstance(node, ast.Call):
                continue
            target = bound_object(node.func, scope)
            if target is UNBOUND:
                if (name := final_name(node.func) or "") in built.values():
                    found.append(f"{path}: {name}")
            elif isinstance(target, type) and target in built:
                found.append(f"{path}: {built[target]}")
    return sorted(found)


def reads_the_caller(value: object) -> bool:
    """Whether *value*'s code, or code nested in it, reads an attribute ``_caller``."""
    function = (
        value.__func__
        if isinstance(value, staticmethod | classmethod)
        else value.fget
        if isinstance(value, property)
        else value
    )
    code = getattr(function, "__code__", None)
    stack = [code] if isinstance(code, CodeType) else []
    while stack:
        current = stack.pop()
        if "_caller" in current.co_names:
            return True
        stack.extend(item for item in current.co_consts if isinstance(item, CodeType))
    return False


def private_callers(
    classes: Iterable[type] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Every class over the session, but the session, that reaches the caller itself.

    Read by object over every class the adapter package defines that is built
    over the session: one that defines ``_send`` or ``_call``, or a member
    whose code reads ``self._caller``, would send through a caller of its own.
    """
    found = (
        [
            cls
            for cls in classes_defined_in(adapter_package_modules())
            if issubclass(cls, SESSION) and cls is not SESSION
        ]
        if classes is None
        else list(classes)
    )
    return {
        cls.__qualname__: held
        for cls in sorted(found, key=lambda cls: cls.__qualname__)
        if (
            held := tuple(
                sorted(
                    name
                    for name, value in vars(cls).items()
                    if name in {"_send", "_call"} or reads_the_caller(value)
                )
            )
        )
    }


def callers_held(root: object) -> list[object]:
    """Every tool caller *root* holds, through containers and the objects in them."""
    found: list[object] = []
    seen: set[int] = set()
    stack = list(vars(root).values())
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, McpToolCaller):
            found.append(item)
        elif isinstance(item, Mapping):
            stack.extend([*item.keys(), *item.values()])
        elif isinstance(item, list | tuple | set | frozenset):
            stack.extend(item)
        elif not isinstance(item, type | ModuleType | FunctionType) and hasattr(
            item, "__dict__"
        ):
            stack.extend(vars(item).values())
    return found


def test_every_class_of_the_adapter_declares_exactly_one_roles_members():
    classes = role_classes()

    assert classes_outside_one_role(classes) == {}
    assert roles_implemented_twice(classes) == {}
    assert roles_implemented_nowhere(classes) == frozenset()
    assert len(classes) == len(declared_by_role())


def test_each_role_class_is_built_over_exactly_the_classes_it_needs():
    assert edge_report(MODULE_TEXT, state=SESSION.__name__, whole=WHOLE) == {}


@pytest.mark.parametrize("role", sorted(declared_by_role()))
def test_each_role_class_answers_its_whole_role(role):
    role_class = getattr(
        inspect.getmodule(LinearMcpTracker), class_per_role(role_classes())[role]
    )

    assert members_declared(port_module_text(), role) <= set(dir(role_class))


def test_only_the_session_constructs_and_it_declares_no_member():
    assert constructing_role_classes() == ()
    assert "__init__" in declared(class_bodies(MODULE_TEXT)[SESSION.__name__])
    assert session_public() == frozenset()
    assert whole_declares() == frozenset()


def test_the_composed_adapter_declares_nothing_at_all():
    """Its body is its docstring: no private, no assignment, no constructor."""
    assert whole_holds() == []


def test_the_composed_adapter_holds_every_role_class_over_one_session():
    resolution = [cls.__name__ for cls in LinearMcpTracker.__mro__]

    assert set(role_classes()) <= set(resolution)
    assert [
        cls for cls in LinearMcpTracker.__mro__[:-1] if "__init__" in vars(cls)
    ] == [SESSION]
    assert all(issubclass(cls, SESSION) for cls in LinearMcpTracker.__mro__[:-2])


def test_one_adapter_object_holds_the_one_caller_it_was_given():
    caller = FakeLinearMcpServer()
    adapter = tracker_over(caller)

    held = [value for value in vars(adapter).values() if value is caller]
    callers = [
        name
        for name, value in vars(adapter).items()
        if value is not caller and isinstance(value, McpToolCaller)
    ]

    assert adapter._caller is caller
    assert len(held) == 1
    assert callers == []


def test_nothing_outside_the_adapters_builds_an_adapter_role_class():
    """Composition holds the composed adapter; a role class alone is built nowhere."""
    assert role_class_constructions(source_tree()) == []


#: Each way a module could build a role class alone, and the class it builds.
PLANTED_CONSTRUCTIONS = {
    "by its own name": (
        "from {module} import {name}\n\n\n"
        "def build(caller):\n    return {name}(caller=caller)\n"
    ),
    "under an import alias": (
        "from {module} import {name} as _Built\n\n\n"
        "def build(caller):\n    return _Built(caller=caller)\n"
    ),
    "through a module alias": (
        "import {module} as linear\n\n\n"
        "def build(caller):\n    return linear.{name}(caller=caller)\n"
    ),
    "under an assignment alias": (
        "from {module} import {name}\n\nBuilt = {name}\n\n\n"
        "def build(caller):\n    return Built(caller=caller)\n"
    ),
}


@pytest.mark.parametrize("form", sorted(PLANTED_CONSTRUCTIONS))
def test_a_role_class_built_under_any_name_is_reported(form):
    name = min(role_classes())
    path = "composition/planted_build.py"
    text = PLANTED_CONSTRUCTIONS[form].format(
        module=LinearMcpTracker.__module__, name=name
    )

    assert role_class_constructions({path: text}) == [f"{path}: {name}"]


def test_no_class_over_the_session_reaches_the_caller_itself():
    session_readers = private_callers([SESSION])

    assert session_readers[SESSION.__qualname__]
    assert private_callers() == {}


def test_a_class_that_sends_through_its_own_caller_is_reported():
    """A role class with its own ``_send``, reading the session's caller."""
    base = getattr(inspect.getmodule(LinearMcpTracker), min(role_classes()))

    async def _send(self: object, tool: str, arguments: object) -> object:
        own = vars(self).setdefault("_own_caller", copy.copy(self._caller))
        return await own.call_tool(name=tool, arguments=arguments)

    revived = type("OwnCaller", (base,), {"_send": _send})

    assert private_callers([revived]) == {"OwnCaller": ("_send",)}


async def test_after_a_scripted_run_the_adapter_holds_only_its_caller():
    """Re-read after use, through containers, not only right after it is built."""
    tracker, caller, _ = scripted_adapter()

    await run(STEPS, tracker, [])
    held = callers_held(tracker)

    assert caller in held
    assert [value for value in held if value is not caller] == []


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
