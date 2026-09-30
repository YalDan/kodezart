"""Which tracker writes a tree makes, and what accounts for each of them.

Two rules state the surface, both read off the roles a tracker is dialled
as rather than listed here.

*Which methods write.*  A public method whose leading name token is a
mutating verb — create, update, upsert, edit, set, post, record, acquire,
renew, release, reset, restore, claim, ensure.  Everything else on those
roles answers a question instead of changing an answer.

*Which writes leave an artifact.*  A write whose every parameter is an
address (``*_key``, ``surfaces``), the holder of a lease, or a lease
duration takes no content and leaves nothing a later reader reads back:
claim and surface-lease bookkeeping.  Every other write puts something on a
surface a consumer will read, which is the thing the verifier exists to
re-read and judge.

The census then asks the tree where each of those writes is called, and
whether the function holding it is one the verifier drives.  Driven is
proven by declared types and by a step reaching the verifier: a name is
used only to WITHHOLD a grant, never to make one, so an unresolved
reference leaves a writer undriven and its write unaccounted for.
"""

import ast
import inspect
from collections.abc import Mapping, Sequence

from kodezart.domain.source_resolution import (
    BASE_DEPTH,
    FUNCTION_NODES,
    OWN_RECEIVERS,
    SourceIndex,
    paired,
)
from kodezart.types.domain.gating import ContentClass
from kodezart.types.domain.write_adoption import (
    CallSite,
    DriveEntry,
    Source,
    WriteCensus,
)

#: The token a write's name starts with.  A role method naming one of these
#: changes what the backend holds; every other one reports what it holds.
WRITE_VERBS = frozenset(
    {
        "acquire",
        "claim",
        "create",
        "edit",
        "ensure",
        "post",
        "record",
        "release",
        "renew",
        "reset",
        "restore",
        "set",
        "update",
        "upsert",
    }
)
#: The function a write taken at module or class level is addressed under:
#: it stands in no function, so it is a site of the module itself.
MODULE_LEVEL = "<module>"
#: What claim and lease bookkeeping is allowed to take beside an address:
#: whose lease it is and how long it runs.  Anything else is content.
LEASE_PARAMETERS = frozenset({"surfaces", "holder", "lease_seconds"})
#: The parameters that say WHERE a write lands and UNDER WHAT PRECONDITION,
#: rather than what it puts there.
ADDRESS_PARAMETERS = frozenset(
    {
        "target",
        "surface",
        "surfaces",
        "ref",
        "holder",
        "lease_seconds",
        "expected",
        "revalidate",
        "authorization",
    }
)


def write_methods(roles: Sequence[type]) -> frozenset[str]:
    """Every write on *roles*, derived from those roles' own members."""
    return frozenset(
        name
        for role in roles
        for name in dir(role)
        if not name.startswith("_")
        and callable(getattr(role, name, None))
        and name.split("_")[0] in WRITE_VERBS
    )


def parameters(method: str, roles: Sequence[type]) -> tuple[str, ...]:
    """What *method* takes, read off the first role declaring it."""
    role = next(role for role in roles if hasattr(role, method))
    signature = inspect.signature(getattr(role, method))
    return tuple(name for name in signature.parameters if name != "self")


def artifact_writes(roles: Sequence[type]) -> frozenset[str]:
    """The writes that leave something a later reader reads back."""
    return frozenset(
        method
        for method in write_methods(roles)
        if not all(
            name.endswith("_key") or name in LEASE_PARAMETERS
            for name in parameters(method, roles)
        )
    )


def content_parameters(method: str, roles: Sequence[type]) -> tuple[str, ...]:
    """What *method* puts on a surface, as against where it puts it."""
    return tuple(
        name
        for name in parameters(method, roles)
        if not name.endswith("_key") and name not in ADDRESS_PARAMETERS
    )


def composes_authored(node: ast.AST) -> bool:
    """Whether this body composes content it authored rather than derived."""
    authored = ContentClass.AUTHORED
    return any(
        isinstance(item, ast.Attribute)
        and item.attr == authored.name
        and isinstance(item.value, ast.Name)
        and item.value.id == type(authored).__name__
        for item in ast.walk(node)
    )


def take_census(
    *,
    sources: Mapping[str, str],
    writes: frozenset[str],
    entry: DriveEntry,
    marker: Source,
) -> WriteCensus:
    """Census every call of *writes* in *sources*, and what accounts for it.

    *entry* says where a step enters the verifier and *marker* which
    function a derived-write declaration names, both derived by the caller
    from the real objects, so this states neither by hand.
    """
    index = SourceIndex(sources)
    sites, values = _call_sites(index, writes)
    driven_holders = _driven_functions(index, entry)
    declarations = _declarations(index, marker)
    driven = frozenset(site for site in sites - values if site.holder in driven_holders)
    held_out = frozenset(
        site
        for site in sites - driven
        if site.method in declarations.get(site.holder, frozenset())
    )
    stale = (
        frozenset(
            CallSite(module=holder.module, function=holder.function, method=method)
            for holder, methods in declarations.items()
            for method in methods
        )
        - held_out
    )
    authored = _authored_sites(index, sites)
    return WriteCensus(
        sites=sites,
        driven=driven,
        held_out=held_out,
        unadopted=(sites - driven - held_out) | (held_out & authored),
        stale=stale,
        authored=authored,
    )


def _call_sites(
    index: SourceIndex, writes: frozenset[str]
) -> tuple[frozenset[CallSite], frozenset[CallSite]]:
    """Every use of *writes* the tree makes, and which of them are values.

    ``self.<write>(…)`` is a role's own implementation of a write reaching
    a sibling of its own, which is the backend seam and not a consumer
    reaching for the tracker.  Every other receiver is a consumer, a class
    that happens to declare a write of the same name included.

    A use is a call, or the write taken as a value: bound to a name, handed
    to a partial or passed along as a callback is a write made later
    through something the census cannot follow, so it is a site of the
    function it is taken in, and never a driven one: the write it stands for
    is made wherever the value is later called, not in the window of the
    function that took it.  One taken at module or class level stands in no
    function and is a site of the module, under ``MODULE_LEVEL``.  The
    second set returned holds every site with such a use.

    A write named by a string is reached by reflection: the name handed to
    the builtin ``getattr`` or to ``operator.methodcaller`` is a site of the
    function the call stands in, whatever the receiver.  A derived-write
    declaration's own arguments name writes too, and are no site: they are
    handed to the declaration, not to either of those.  The name handed to
    ``getattr`` is a call only where the ``getattr`` is itself called on the
    spot; one handed to ``methodcaller`` is always a value.
    """
    sites: set[CallSite] = set()
    values: set[CallSite] = set()
    for method in writes:
        for holder, reference in index.references(method):
            if (
                not isinstance(reference, ast.Attribute)
                or not isinstance(reference.ctx, ast.Load)
                or (
                    isinstance(reference.value, ast.Name)
                    and reference.value.id in OWN_RECEIVERS
                )
            ):
                continue
            if holder is not None:
                site = CallSite(
                    module=holder.module, function=holder.function, method=method
                )
                sites.add(site)
                if index.call_of(reference) is None:
                    values.add(site)
                continue
            module = index.unheld_module(reference)
            if module is not None:
                site = CallSite(module=module, function=MODULE_LEVEL, method=method)
                sites.add(site)
                values.add(site)
    for module, holder, call in index.every_call():
        reflected = _reflected(index, module, holder, call)
        if reflected is None or reflected[0] not in writes:
            continue
        site = CallSite(
            module=module,
            function=MODULE_LEVEL if holder is None else holder.function,
            method=reflected[0],
        )
        sites.add(site)
        if not reflected[1] or index.call_of(call) is None:
            values.add(site)
    return frozenset(sites), frozenset(values)


def _reflected(
    index: SourceIndex, module: str, holder: Source | None, call: ast.Call
) -> tuple[str, bool] | None:
    """The method a reflective call names by a string, if it is one.

    Beside the name, whether the call returns the write itself, as
    ``getattr`` does, rather than a caller of it, as ``methodcaller`` does.

    ``getattr(receiver, "<name>", …)`` and ``operator.methodcaller("<name>",
    …)``, each resolved as the builtin or standard-library function rather
    than by spelling: a name the package itself binds where the call stands
    is not either of them.
    """
    callee = call.func
    if (
        isinstance(callee, ast.Name)
        and callee.id == "getattr"
        and index.unbound(module, holder, callee.id)
    ):
        named = call.args[1] if len(call.args) > 1 else None
        returns_write = True
    elif (
        isinstance(callee, ast.Name)
        and callee.id == "methodcaller"
        and index.unbound(module, holder, callee.id)
    ) or (
        isinstance(callee, ast.Attribute)
        and callee.attr == "methodcaller"
        and isinstance(callee.value, ast.Name)
        and callee.value.id == "operator"
        and index.unbound(module, holder, callee.value.id)
    ):
        named = call.args[0] if call.args else None
        returns_write = False
    else:
        return None
    if isinstance(named, ast.Constant) and isinstance(named.value, str):
        return named.value, returns_write
    return None


def _authored_sites(
    index: SourceIndex, sites: frozenset[CallSite]
) -> frozenset[CallSite]:
    """The sites whose writer composes content it authored.

    The writer is the class holding the call, or the module for a
    module-level function: authorship belongs to whoever composed the text,
    which is routinely a sibling method of the one that puts it on the
    surface.
    """
    scanned: dict[Source | None, bool] = {}
    authored: set[CallSite] = set()
    for site in sites:
        owner = index.owner(site.holder)
        scope = owner if owner is not None else Source(module=site.module, function="")
        if scope not in scanned:
            scanned[scope] = composes_authored(
                index.classes[owner] if owner is not None else index.trees[site.module]
            )
        if scanned[scope]:
            authored.add(site)
    return frozenset(authored)


def _declarations(
    index: SourceIndex, marker: Source
) -> Mapping[Source, frozenset[str]]:
    """Each function's derived-write declaration, resolved to *marker*."""
    declared: dict[Source, frozenset[str]] = {}
    for holder, node in index.functions.items():
        methods: set[str] = set()
        for decorator in node.decorator_list:
            if (
                not isinstance(decorator, ast.Call)
                or index.resolve(holder, decorator) != marker
            ):
                continue
            methods.update(
                argument.value
                for argument in decorator.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            )
        if methods:
            declared[holder] = frozenset(methods)
    return declared


class _Grant:
    """What becomes driven, grounded or a sink once one sink is reached.

    *appliers* keeps each applier a grounded constructor was handed beside
    the node that handed it, because that one mention is the grant and any
    other mention of the applier's name is a use the grant cannot vouch for.
    *handed* keeps the step class each applier was handed to and the
    parameter it was handed under, because the step's field holding it is
    another way to reach it.
    """

    __slots__ = ("appliers", "driven", "grounded", "handed", "sinks")

    def __init__(
        self,
        *,
        grounded: frozenset[Source],
        driven: frozenset[Source],
        sinks: frozenset[tuple[Source, str]],
        appliers: frozenset[tuple[Source, int]] = frozenset(),
        handed: frozenset[tuple[Source, Source, str]] = frozenset(),
    ) -> None:
        self.grounded = grounded
        self.driven = driven
        self.sinks = sinks
        self.appliers = appliers
        self.handed = handed


class _Growth:
    """One fixed point of the grants: what is driven, and by which grant."""

    __slots__ = ("appliers", "driven", "handed", "steps")

    def __init__(
        self,
        *,
        driven: frozenset[Source],
        appliers: Mapping[Source, frozenset[int]],
        steps: frozenset[Source],
        handed: Mapping[Source, frozenset[tuple[Source, str]]],
    ) -> None:
        self.driven = driven
        self.appliers = appliers
        self.steps = steps
        self.handed = handed


def _driven_functions(index: SourceIndex, entry: DriveEntry) -> frozenset[Source]:
    """The functions the verifier drives, grown to a fixed point from *entry*.

    A step handed to the verifier grounds its class, and the member that
    class answers the verifier with is driven.  What a grounded step is
    built around is driven with it: the applier a writing step is
    constructed over IS the write the loop re-reads.  A caller's own
    parameter handed on to a sink makes that parameter a sink in turn, so a
    factory two levels away from the verifier is followed rather than
    guessed at.  Finally, a function is driven when every reference to its
    name anywhere in the tree resolves, at least one resolves to it, and
    every call that resolves to it stands in a driven function.

    Construction alone is never the whole grant.  Once the grants have
    grown, a granted applier or step member that any undriven function
    calls loses its grant, and so does an applier whose name is mentioned
    anywhere but in a call or the constructor argument that granted it.  A
    call is weighed against a step member however it is typed: resolved to
    the member itself, to a nominal base's member it overrides, or to the
    step protocol's member anywhere but in the verifier.  An applier is
    also reached through the step field holding it, so a read of that field
    counts its function as a caller of the applier.  The growth is then
    taken again without what was withdrawn, until nothing more is, because
    a withdrawn grant can take delegations resting on it down too.
    """
    handed = _handed_arguments(index)
    overrides = _overrides(index)
    delegations = _delegations(index, overrides)
    callers = _resolved_callers(index)
    withheld: frozenset[Source] = frozenset()
    while True:
        growth = _grow(index, entry, handed, delegations, withheld)
        withdrawn = _withdrawn(index, entry, growth, callers, overrides)
        if withdrawn <= withheld:
            return growth.driven
        withheld |= withdrawn


def _grow(
    index: SourceIndex,
    entry: DriveEntry,
    handed: Mapping[tuple[Source, str], tuple[tuple[Source, ast.expr], ...]],
    delegations: Mapping[Source, frozenset[Source]],
    withheld: frozenset[Source],
) -> _Growth:
    """Grow the grants from *entry*, granting nothing in *withheld*."""
    sinks = {(entry.verifier, entry.step_parameter)}
    grounded: set[Source] = set()
    driven: set[Source] = set()
    appliers: dict[Source, set[int]] = {}
    handed_to: dict[Source, set[tuple[Source, str]]] = {}
    steps: set[Source] = set()
    spent: set[tuple[Source, str]] = set()
    settled: set[Source] = set()
    changed = True
    while changed:
        changed = False
        for sink in tuple(sinks):
            if sink in spent:
                continue
            spent.add(sink)
            changed = True
            for holder, argument in handed.get(sink, ()):
                grant = _grant(index, holder, argument)
                grounded |= grant.grounded
                sinks |= grant.sinks
                for applier, mention in grant.appliers:
                    if applier not in withheld:
                        appliers.setdefault(applier, set()).add(mention)
                        driven.add(applier)
                for applier, owner, parameter in grant.handed:
                    if applier not in withheld:
                        handed_to.setdefault(applier, set()).add((owner, parameter))
        for owner in tuple(grounded):
            if owner in settled:
                continue
            settled.add(owner)
            changed = True
            step = _step_grant(index, owner, entry.step_method)
            if step.driven & withheld:
                continue
            driven |= step.driven
            steps |= step.driven
            sinks |= step.sinks
        for holder, calling in delegations.items():
            if holder not in driven and holder not in withheld and calling <= driven:
                driven.add(holder)
                changed = True
    return _Growth(
        driven=frozenset(driven),
        appliers={applier: frozenset(ids) for applier, ids in appliers.items()},
        steps=frozenset(steps),
        handed={applier: frozenset(to) for applier, to in handed_to.items()},
    )


def _withdrawn(
    index: SourceIndex,
    entry: DriveEntry,
    growth: _Growth,
    callers: Mapping[Source, frozenset[Source]],
    overrides: Mapping[Source, frozenset[Source]],
) -> frozenset[Source]:
    """The grants *growth* made that something outside a write-back uses.

    A step member is reached by name all over the tree, through the
    protocol, so only its resolved calls are weighed: those resolved to it,
    to a base member it overrides, and to the step protocol's own member
    from anywhere but the verifier, since that call may be this step at
    run time.  An applier is a local, so every mention of its name where it
    is visible is weighed too, and so is every read of the step field it
    was handed to, whose function is a caller of it.
    """
    protocol = Source(
        module=entry.step.module, function=f"{entry.step.function}.{entry.step_method}"
    )
    overridden: dict[Source, set[Source]] = {}
    for base, below in overrides.items():
        for override in below:
            overridden.setdefault(override, set()).add(base)

    def weighed(target: Source) -> frozenset[Source]:
        found = callers.get(target, frozenset())
        return found - {entry.verifier} if target == protocol else found

    withdrawn: set[Source] = set()
    for granted in (*growth.appliers, *growth.steps):
        reached = {granted, *overridden.get(granted, ())}
        if granted in growth.steps:
            reached.add(protocol)
        if not all(weighed(target) <= growth.driven for target in reached):
            withdrawn.add(granted)
    for applier, handed in growth.handed.items():
        if not _field_readers(index, handed) <= growth.driven:
            withdrawn.add(applier)
    for applier, granting in growth.appliers.items():
        name = applier.function.rsplit(".", 1)[-1]
        for holder, reference in index.references(name):
            if (
                holder is None
                or not isinstance(reference, ast.Name)
                or index.nested(holder, name) != applier
                or id(reference) in granting
            ):
                continue
            call = index.call_of(reference)
            if call is None or index.resolve(holder, call) != applier:
                withdrawn.add(applier)
                break
    return frozenset(withdrawn)


def _resolved_callers(index: SourceIndex) -> Mapping[Source, frozenset[Source]]:
    """For each function, every function holding a call resolved to it."""
    found: dict[Source, set[Source]] = {}
    for holder in index.functions:
        for call in index.direct_calls(holder):
            target = index.resolve(holder, call)
            if target is not None:
                found.setdefault(target, set()).add(holder)
    return {target: frozenset(holders) for target, holders in found.items()}


def _field_readers(
    index: SourceIndex, handed: frozenset[tuple[Source, str]]
) -> frozenset[Source]:
    """Every function reading a step field that holds an applier.

    The field is the parameter the applier was handed under, or, where the
    step has an initializer, every attribute that initializer stores that
    parameter in.  A read counts only on a receiver typed as that step, its
    own methods' ``self`` included.
    """
    readers: set[Source] = set()
    for owner, parameter in handed:
        initializer = index.functions.get(
            Source(module=owner.module, function=f"{owner.function}.__init__")
        )
        fields = (
            {parameter}
            if initializer is None
            else _holding_attributes(initializer, parameter)
        )
        for field in fields:
            for holder, reference in index.references(field):
                if (
                    holder is not None
                    and isinstance(reference, ast.Attribute)
                    and isinstance(reference.ctx, ast.Load)
                    and index.type_of(holder, reference.value) == owner
                ):
                    readers.add(holder)
    return frozenset(readers)


def _holding_attributes(
    initializer: ast.FunctionDef | ast.AsyncFunctionDef, parameter: str
) -> set[str]:
    """The attributes *initializer* stores its *parameter* in."""
    held: set[str] = set()
    for statement in ast.walk(initializer):
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            for part, value in paired(target, statement.value):
                if (
                    isinstance(part, ast.Attribute)
                    and isinstance(part.value, ast.Name)
                    and part.value.id in OWN_RECEIVERS
                    and isinstance(value, ast.Name)
                    and value.id == parameter
                ):
                    held.add(part.attr)
    return held


def _handed_arguments(
    index: SourceIndex,
) -> Mapping[tuple[Source, str], tuple[tuple[Source, ast.expr], ...]]:
    """Every argument the tree hands to a named parameter, and from where.

    Resolved once for the whole tree, because the fixed point below asks
    the same question of the same calls as it grows and a second resolution
    pass over the installed package is the largest cost here.
    """
    handed: dict[tuple[Source, str], list[tuple[Source, ast.expr]]] = {}
    for holder in index.functions:
        for call in index.direct_calls(holder):
            target = index.resolve(holder, call)
            if target is None:
                continue
            for name, argument in index.arguments(target, call):
                if name is not None:
                    handed.setdefault((target, name), []).append((holder, argument))
    return {slot: tuple(found) for slot, found in handed.items()}


def _grant(index: SourceIndex, holder: Source, argument: ast.expr) -> _Grant:
    """What handing *argument* to a sink says about the tree around it."""
    grounded: set[Source] = set()
    appliers: set[tuple[Source, int]] = set()
    handed: set[tuple[Source, Source, str]] = set()
    sinks: set[tuple[Source, str]] = set()
    parameters_here = index.parameter_names(holder)
    bindings = index.bindings(holder)
    pending = [argument]
    seen: set[int] = set()
    while pending:
        expression = pending.pop()
        if id(expression) in seen:
            continue
        seen.add(id(expression))
        if isinstance(expression, ast.Call):
            target = index.resolve(holder, expression)
            if target is None or target not in index.classes:
                continue
            grounded.add(target)
            for parameter, passed in index.arguments(target, expression):
                if isinstance(passed, ast.Name):
                    local = index.nested(holder, passed.id)
                    if local is None:
                        continue
                    appliers.add((local, id(passed)))
                    if parameter is not None:
                        handed.add((local, target, parameter))
        elif isinstance(expression, ast.Name):
            if expression.id in parameters_here:
                sinks.add((holder, expression.id))
            else:
                pending.extend(bindings.get(expression.id, ()))
    return _Grant(
        grounded=frozenset(grounded),
        driven=frozenset(applier for applier, _ in appliers),
        sinks=frozenset(sinks),
        appliers=frozenset(appliers),
        handed=frozenset(handed),
    )


def _step_grant(index: SourceIndex, owner: Source, step_method: str) -> _Grant:
    """What a grounded step class drives, and what it forwards its write to."""
    address = Source(module=owner.module, function=f"{owner.function}.{step_method}")
    if address not in index.functions:
        return _Grant(grounded=frozenset(), driven=frozenset(), sinks=frozenset())
    forwarded: set[tuple[Source, str]] = set()
    initializer = index.functions.get(
        Source(module=owner.module, function=f"{owner.function}.__init__")
    )
    for call in index.direct_calls(address):
        callee = call.func
        if (
            not isinstance(callee, ast.Attribute)
            or callee.attr != step_method
            or not isinstance(callee.value, ast.Attribute)
            or not isinstance(callee.value.value, ast.Name)
            or callee.value.value.id not in OWN_RECEIVERS
            or initializer is None
        ):
            continue
        forwarded |= _held_parameters(initializer, callee.value.attr, owner)
    return _Grant(
        grounded=frozenset(),
        driven=frozenset({address}),
        sinks=frozenset(forwarded),
    )


def _held_parameters(
    initializer: ast.FunctionDef | ast.AsyncFunctionDef,
    attribute: str,
    owner: Source,
) -> set[tuple[Source, str]]:
    """The constructor parameters *initializer* stores under *attribute*."""
    held: set[tuple[Source, str]] = set()
    for statement in ast.walk(initializer):
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            for part, value in paired(target, statement.value):
                if (
                    isinstance(part, ast.Attribute)
                    and part.attr == attribute
                    and isinstance(value, ast.Name)
                ):
                    held.add((owner, value.id))
    return held


def _delegations(
    index: SourceIndex, overrides: Mapping[Source, frozenset[Source]]
) -> Mapping[Source, frozenset[Source]]:
    """For each function whose every reference resolves, who calls it.

    A reference nothing resolves keeps the function undriven however its
    resolved callers stand, which is what makes a name usable only to
    withhold a grant.  A dunder is left out: it answers a protocol rather
    than being reached by name.

    A call resolved to a method is also a call of every override of that
    method: typed as the base, it may be the override that answers at run
    time, so the override carries that caller too.
    """
    delegations: dict[Source, frozenset[Source]] = {}
    for holder, node in index.functions.items():
        if node.name.startswith("__"):
            continue
        references = index.references(node.name)
        if not references:
            continue
        callers: set[Source] = set()
        resolved = True
        for caller, reference in references:
            call = index.call_of(reference)
            target = (
                None if caller is None or call is None else index.resolve(caller, call)
            )
            if caller is None or target is None:
                resolved = False
                break
            if target == holder or holder in overrides.get(target, frozenset()):
                callers.add(caller)
        if resolved and callers:
            delegations[holder] = frozenset(callers)
    return delegations


def _overrides(index: SourceIndex) -> Mapping[Source, frozenset[Source]]:
    """For each method, the methods of the same name its subclasses define.

    A subclass is a class whose bases, resolved as annotations are and
    followed to ``BASE_DEPTH``, reach the class defining the method.  Only
    nominal subclassing is read: a class answering a protocol structurally
    names no base to follow.
    """
    found: dict[Source, set[Source]] = {}
    for owner, node in index.classes.items():
        ancestors = _ancestors(index, owner)
        for item in node.body:
            if not isinstance(item, FUNCTION_NODES):
                continue
            own = Source(module=owner.module, function=f"{owner.function}.{item.name}")
            for ancestor in ancestors:
                overridden = Source(
                    module=ancestor.module, function=f"{ancestor.function}.{item.name}"
                )
                if overridden in index.functions:
                    found.setdefault(overridden, set()).add(own)
    return {method: frozenset(below) for method, below in found.items()}


def _ancestors(index: SourceIndex, owner: Source) -> frozenset[Source]:
    """Every class *owner*'s resolved bases reach, to ``BASE_DEPTH``."""
    reached: set[Source] = set()
    frontier = [owner]
    for _ in range(BASE_DEPTH):
        following: list[Source] = []
        for current in frontier:
            for base in index.classes[current].bases:
                resolved = index.annotated(current.module, base)
                if resolved is not None and resolved not in reached:
                    reached.add(resolved)
                    following.append(resolved)
        if not following:
            break
        frontier = following
    reached.discard(owner)
    return frozenset(reached)
