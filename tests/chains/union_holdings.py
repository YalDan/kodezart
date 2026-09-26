"""What the union step may hold, and the walk that reads what it does hold.

Shared by the cases that build the step and walk it: the exit scenarios
walk the step each one drove, after it verified, and the isolation cases
walk the step a production call site builds.  One rule, so both read the
same thing.

Everything the walk reaches must be one of these: a record (a value of an
exact scalar type, or of a class the records package defines); a routine or
a builtin container, which the walk looks inside; one of the step's own
parts; one of the registered parts; or an implementation of a port the step
or a registered part is typed to hold, which answers nothing its ports do
not declare.  A registered part's port is admitted only when it declares no
member the forge client answers and no merge.  Anything else is refused,
whatever its members are called.

What the walk reads, and what it does not: it reads what an object holds in
its instance attributes (all of them, dunder names included, except a
routine's own metadata), in the slots of every class of its MRO that is not
a builtin, in the class attributes of every class this repository writes,
in the items and keys of a collection, and through each routine's
receiver, function, closure cells, defaults and ``__wrapped__``.  It reads
an object as it is when walked.  Outside its reach, and so stated: a value
handed across a function boundary, where the other function is not
resolved here (a module-level global a function reads when it runs, or a
value a helper returns); and a binding made only when a function runs
(``setattr`` inside a function body) until that function has run — which is
why the exit and return scenarios walk the step after it verified.  The
isolation cases hold both as unseen.
"""

import asyncio
import importlib
from collections import deque
from collections.abc import Collection, Mapping
from datetime import datetime
from enum import Enum
from functools import partial
from types import (
    BuiltinFunctionType,
    FunctionType,
    GetSetDescriptorType,
    MemberDescriptorType,
    MethodType,
    ModuleType,
)
from typing import get_type_hints

import kodezart.types
from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.core import protocols
from kodezart.services.lane_records import LaneRecordReader

#: The union step's own modules: where every exit of verifying is written.
UNION_MODULES: tuple[str, ...] = (
    "kodezart.chains.delivery_coordinator",
    "kodezart.services.union_tick",
    "kodezart.services.union_composition",
    "kodezart.services.union_identity",
)


def is_port(value: object) -> bool:
    """A protocol the ports module declares: what a collaborator is typed as."""
    return (
        isinstance(value, type)
        and value.__module__ == protocols.__name__
        and getattr(value, "_is_protocol", False)
    )


def declared(port: type) -> frozenset[str]:
    """Every member *port* declares, inherited members and properties included.

    Read off the protocol's own record of its members, not off ``callable``,
    which a property on a port fails, and not off ``dir``, which cannot tell
    a declared member from the machinery every class carries.
    """
    return frozenset(port.__protocol_attrs__)


def ports_of(cls: type) -> dict[str, type]:
    """Every constructor parameter of *cls* annotated with a port, by name."""
    return {
        name: hint
        for name, hint in get_type_hints(cls.__init__).items()
        if is_port(hint)
    }


def public_callables(subject: object) -> frozenset[str]:
    """Every public name *subject* answers to that can be called.

    Read across the whole MRO of whatever is handed in and never off
    ``vars``, which is one class body alone.  ``dir`` is complete only for a
    class that answers no name through ``__getattr__``, ``__getattribute__``
    or ``__dir__``, so the holdings rule and the query-double rows refuse
    those first (``answers_by_hook``).
    """
    return frozenset(
        name
        for name in dir(subject)
        if not name.startswith("_") and callable(getattr(subject, name, None))
    )


#: The methods that let a class answer a name ``dir`` does not report.
ANSWERING_HOOKS: tuple[str, ...] = ("__getattr__", "__getattribute__", "__dir__")


def answers_by_hook(cls: type) -> bool:
    """Whether any class in *cls*'s MRO but ``object`` defines one of the hooks."""
    return any(
        hook in vars(base)
        for base in cls.__mro__
        if base is not object
        for hook in ANSWERING_HOOKS
    )


#: The union step's own parts: every class its modules define, read off them.
STEP_PARTS: tuple[type, ...] = tuple(
    value
    for name in UNION_MODULES
    for value in vars(importlib.import_module(name)).values()
    if isinstance(value, type) and value.__module__ == name
)

#: Every port a part is constructed with, read off the constructors' own
#: annotations: the collaborators the step is typed to hold.
STEP_PORTS: tuple[type, ...] = tuple(
    dict.fromkeys(port for part in STEP_PARTS for port in ports_of(part).values())
)

#: Where the product's records are defined: a value of a class from this
#: package is data the step carries, not a collaborator it can ask.
RECORD_PACKAGE: str = kodezart.types.__name__

#: The plain values a record is built from, as exact types: an instance of
#: a subclass is judged like any other object, so a class deriving from
#: ``str`` cannot carry a collaborator past the walk.  Each is a type some
#: value the step holds has; the isolation cases require that.
SCALARS: tuple[type, ...] = (type(None), bool, int, float, str, datetime)

#: The routines the step holds, as exact types: each is walked into, never
#: trusted.  Each is a type some value the step holds has.
ROUTINES: tuple[type, ...] = (FunctionType, staticmethod)

#: The containers the step holds, as exact types: walked through their items
#: and keys, which is all the state each of them has.  A container whose
#: state is more than its items (a ``defaultdict``'s factory, a
#: ``ChainMap``'s maps) is not one of them, so it is refused rather than half
#: read.  Each is a type some value the step holds has.
COLLECTIONS: tuple[type, ...] = (dict, list, tuple, frozenset, set, deque)

#: The packages whose class bodies the walk reads for class attributes: the
#: code this repository writes.  A library class's own body is its own.
OWN_PACKAGES: frozenset[str] = frozenset({"kodezart", "tests"})

#: The two parts the step holds that are neither its own classes nor a port,
#: keyed by type identity, each with its reason.  Both are still walked into.
REGISTERED_PARTS: dict[type, str] = {
    asyncio.Lock: "UnionTick serialises its ticks on one: scheduling state",
    LaneRecordReader: (
        "the shipped ref reader reads each lane's recorded refs through it"
    ),
}


#: The forge client the product ships: what a port reaching the forge answers.
FORGE_CLIENT: type = GitHubAPIClient


def reaches_the_forge(port: type) -> bool:
    """Whether *port* declares a member the forge client answers, or a merge.

    A merge is read off the member's name in any spelling, so a port that
    grew one is caught whatever the verb around it.
    """
    return any(
        hasattr(FORGE_CLIENT, member) or "merge" in member.lower()
        for member in declared(port)
    )


def part_ports(parts: Collection[type]) -> tuple[type, ...]:
    """Every port a registered part's constructor is typed on, off the forge.

    Read off each constructor's own annotations, as the step's are.  A port
    that reaches the forge is never admitted this way, whatever part names
    it: a registered part is a permission for that part, not for a forge.
    """
    return tuple(
        dict.fromkeys(
            port
            for part in parts
            for port in ports_of(part).values()
            if not reaches_the_forge(port)
        )
    )


#: Every port the walk admits an implementation of: the step's own, then
#: the registered parts' (``LaneRecordReader`` reads its lanes' records
#: through a comment reader it is constructed with).
HELD_PORTS: tuple[type, ...] = tuple(
    dict.fromkeys((*STEP_PORTS, *part_ports(REGISTERED_PARTS)))
)


def is_record(value: object) -> bool:
    """An exact scalar, or a value (or an Enum class) the records package defines."""
    kind = value if isinstance(value, type) and issubclass(value, Enum) else type(value)
    return type(value) in SCALARS or (
        kind.__module__ == RECORD_PACKAGE
        or kind.__module__.startswith(f"{RECORD_PACKAGE}.")
    )


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


#: What holds a callable's code rather than a value: its own metadata is
#: names, text and annotations, so only ``__wrapped__`` is read off it.
_ROUTINE_KINDS: tuple[type, ...] = (
    FunctionType,
    MethodType,
    BuiltinFunctionType,
    staticmethod,
    classmethod,
    property,
    partial,
)


def contents(value: object) -> list[object]:
    """Every object *value* holds one step down, by each route Python stores one.

    Every instance attribute, dunder names included, except on a routine,
    whose dunder-named attributes are its own metadata and of which only
    ``__wrapped__`` is read; the slots of every class in the MRO that is not
    a builtin, a record's private and extra state included; class attributes
    across the MRO of a class this repository writes, when the value is not a
    record, other than the accessors a class makes for its own ``__dict__``;
    every item and key of a collection; a bound method's ``__self__`` and
    ``__func__``; a builtin's ``__self__``; a function's closure cells (not
    the ``__class__`` cell ``super()`` makes), defaults and keyword defaults; a
    static or class method's function; a property's accessors; a partial's
    function, arguments and keywords.  A value of an exact scalar type, a
    class or a module is not walked into: a definition is not a collaborator.
    A function's globals are not walked: that is a stated limit.
    """
    kind = type(value)
    if kind in SCALARS or isinstance(value, type | ModuleType):
        return []
    found: list[object] = []
    if isinstance(value, FunctionType):
        cells = zip(value.__code__.co_freevars, value.__closure__ or (), strict=True)
        for name, cell in cells:
            if name != "__class__":
                try:
                    found.append(cell.cell_contents)
                except ValueError:
                    continue
        found.extend(value.__defaults__ or ())
        found.extend((value.__kwdefaults__ or {}).values())
    elif isinstance(value, MethodType):
        found.extend((value.__self__, value.__func__))
    elif isinstance(value, BuiltinFunctionType):
        found.append(value.__self__)
    elif isinstance(value, staticmethod | classmethod):
        found.append(value.__func__)
    elif isinstance(value, property):
        found.extend((value.fget, value.fset, value.fdel))
    elif isinstance(value, partial):
        found.extend((value.func, *value.args, *value.keywords.values()))
    if isinstance(value, Mapping):
        found.extend((*value.keys(), *value.values()))
    elif isinstance(value, Collection):
        found.extend(value)
    routine = isinstance(value, _ROUTINE_KINDS)
    if hasattr(value, "__dict__"):
        found.extend(
            attribute
            for name, attribute in vars(value).items()
            if not (routine and _is_dunder(name)) or name == "__wrapped__"
        )
    for base in kind.__mro__:
        if base.__module__ == "builtins":
            continue
        for attribute in vars(base).values():
            if isinstance(attribute, MemberDescriptorType):
                try:
                    found.append(attribute.__get__(value))
                except AttributeError:
                    continue
    if not is_record(value):
        for base in kind.__mro__:
            if is_port(base) or base.__module__.split(".")[0] not in OWN_PACKAGES:
                continue
            found.extend(
                attribute
                for attribute in vars(base).values()
                if not isinstance(
                    attribute, MemberDescriptorType | GetSetDescriptorType
                )
            )
    return found


def held_by(subject: object, *, besides: Collection[object] = ()) -> list[object]:
    """Every object the subject holds, transitively, itself included.

    Bounded by the objects reachable from *subject* through ``contents``,
    each visited once.  What *besides* names, by identity, is neither
    reported nor walked into.
    """
    found: list[object] = []
    seen: set[int] = {id(value) for value in besides}
    pending: list[object] = [subject]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        found.append(value)
        pending.extend(contents(value))
    return found


def allowed_as(value: object) -> str | None:
    """What the step may hold *value* as, or None when it may not hold it.

    A record; a routine or a builtin container, which the walk looks inside;
    one of the step's own parts; one of the registered parts; or an
    implementation of a port the step or a registered part is typed to hold
    (``HELD_PORTS``), which answers nothing its ports do not declare and
    answers no name ``dir`` cannot see.  Anything
    else is refused whatever its members are called: a forge client or
    double, a hand-built handle, an instance of a scalar's subclass, a
    container whose state is more than its items, a port implementation that
    grew a method, and a class or a module, whose types answer names through
    ``__getattribute__``.

    Neither a record's nor a part's own methods are measured here; the
    isolation cases pin both by type (``WRITTEN_SURFACES``).
    """
    kind = type(value)
    if is_record(value):
        return "record"
    if kind in ROUTINES:
        return "routine"
    if kind in COLLECTIONS:
        return "collection"
    if kind in STEP_PARTS:
        return "part"
    if kind in REGISTERED_PARTS:
        return "registered"
    ports = [port for port in HELD_PORTS if isinstance(value, port)]
    if (
        ports
        and not answers_by_hook(kind)
        and public_callables(value) <= frozenset().union(*map(declared, ports))
    ):
        return "port"
    return None


def refused(held: list[object]) -> list[str]:
    """The class of everything in *held* the step may not hold."""
    return [
        f"{type(value).__module__}.{type(value).__qualname__}"
        for value in held
        if allowed_as(value) is None
    ]


def written_surface(cls: type) -> frozenset[str]:
    """Every callable *cls* answers that this repository's code defines.

    Read off the class bodies of *cls*'s MRO written under ``OWN_PACKAGES``,
    dunder names included, so a method a record or a part grows is here
    whatever it is called.  A body entry that is the very object a library
    base carries under that name (what ``Enum`` copies onto each subclass) is
    the library's, not written here.
    """
    library = [
        vars(base)
        for base in cls.__mro__
        if base.__module__.split(".")[0] not in OWN_PACKAGES
    ]
    return frozenset(
        name
        for base in cls.__mro__
        if base.__module__.split(".")[0] in OWN_PACKAGES
        for name, attribute in vars(base).items()
        if (isinstance(attribute, _ROUTINE_KINDS) or callable(attribute))
        and not any(body.get(name) is attribute for body in library)
    )
