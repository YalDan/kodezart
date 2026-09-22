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
    sites = _call_sites(index, writes)
    driven_holders = _driven_functions(index, entry)
    declarations = _declarations(index, marker)
    driven = frozenset(site for site in sites if site.holder in driven_holders)
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


def _call_sites(index: SourceIndex, writes: frozenset[str]) -> frozenset[CallSite]:
    """Every call of *writes* the tree makes through a receiver of its own.

    ``self.<write>(…)`` is a role's own implementation of a write reaching
    a sibling of its own, which is the backend seam and not a consumer
    reaching for the tracker.  Every other receiver is a consumer, a class
    that happens to declare a write of the same name included.
    """
    sites: set[CallSite] = set()
    for holder in index.functions:
        for call in index.direct_calls(holder):
            callee = call.func
            if not isinstance(callee, ast.Attribute) or callee.attr not in writes:
                continue
            if isinstance(callee.value, ast.Name) and callee.value.id in OWN_RECEIVERS:
                continue
            sites.add(
                CallSite(
                    module=holder.module,
                    function=holder.function,
                    method=callee.attr,
                )
            )
    return frozenset(sites)


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
    """What becomes driven, grounded or a sink once one sink is reached."""

    __slots__ = ("driven", "grounded", "sinks")

    def __init__(
        self,
        *,
        grounded: frozenset[Source],
        driven: frozenset[Source],
        sinks: frozenset[tuple[Source, str]],
    ) -> None:
        self.grounded = grounded
        self.driven = driven
        self.sinks = sinks


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
    """
    handed = _handed_arguments(index)
    sinks = {(entry.verifier, entry.step_parameter)}
    grounded: set[Source] = set()
    driven: set[Source] = set()
    delegations = _delegations(index)
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
                driven |= grant.driven
                sinks |= grant.sinks
        for owner in tuple(grounded):
            if owner in settled:
                continue
            settled.add(owner)
            changed = True
            step = _step_grant(index, owner, entry.step_method)
            driven |= step.driven
            sinks |= step.sinks
        for holder, callers in delegations.items():
            if holder not in driven and callers <= driven:
                driven.add(holder)
                changed = True
    return frozenset(driven)


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
    driven: set[Source] = set()
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
            for passed in (
                *expression.args,
                *(word.value for word in expression.keywords),
            ):
                if isinstance(passed, ast.Name):
                    local = index.nested(holder, passed.id)
                    if local is not None:
                        driven.add(local)
        elif isinstance(expression, ast.Name):
            if expression.id in parameters_here:
                sinks.add((holder, expression.id))
            else:
                pending.extend(bindings.get(expression.id, ()))
    return _Grant(
        grounded=frozenset(grounded),
        driven=frozenset(driven),
        sinks=frozenset(sinks),
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


def _delegations(index: SourceIndex) -> Mapping[Source, frozenset[Source]]:
    """For each function whose every reference resolves, who calls it.

    A reference nothing resolves keeps the function undriven however its
    resolved callers stand, which is what makes a name usable only to
    withhold a grant.  A dunder is left out: it answers a protocol rather
    than being reached by name.
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
            if target == holder:
                callers.add(caller)
        if resolved and callers:
            delegations[holder] = frozenset(callers)
    return delegations
