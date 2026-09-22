"""Resolve a name in parsed production source to the definition it names.

Every answer here is grounded in something the source states: an enclosing
definition, a module-level definition, an import inside the package, a
declared annotation, or a constructor call.  A name alone is never an
answer, because two modules may spell the same name for two different
things and a reader that guessed would grant adoption to whichever it saw
first.  Where the source does not say, this says nothing, and the caller
that wanted a grant does not get one.

The whole tree is indexed in one traversal per module, and every derived
reading is kept, because the callers ask the same questions of the same
definitions repeatedly and a parse of the installed package is the largest
cost here.
"""

import ast
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from kodezart.types.domain.write_adoption import Source

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
#: The two shapes a function definition takes in a parsed module.
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
#: How far a member read follows a class's own bases before giving up.
BASE_DEPTH = 10
#: The receivers that name the class a method is declared on.
OWN_RECEIVERS = frozenset({"self", "cls"})


def dotted_name(module: str) -> str:
    """The import path of a package module addressed by its posix path."""
    parts = ("kodezart", *PurePosixPath(module).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class SourceIndex:
    """Parsed package source, addressed by definition rather than by name."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.trees: dict[str, ast.Module] = {
            module: ast.parse(text) for module, text in sorted(sources.items())
        }
        self.functions: dict[Source, FunctionNode] = {}
        self.classes: dict[Source, ast.ClassDef] = {}
        self._parent: dict[Source, Source | None] = {}
        self._owner: dict[Source, Source | None] = {}
        self._calls: dict[Source, list[ast.Call]] = {}
        self._assigns: dict[Source, list[ast.Assign]] = {}
        self._references: dict[str, list[tuple[Source | None, ast.expr]]] = {}
        self._calls_by_callee: dict[int, ast.Call] = {}
        self._imports: dict[str, dict[str, tuple[str, str]]] = {}
        self._module_of: dict[str, str] = {
            dotted_name(module): module for module in self.trees
        }
        for module, tree in self.trees.items():
            self._imports[module] = {}
            self._index(module, tree, (), None, None, None)
        self._attribute_types: dict[tuple[Source, str], Source | None] = {}
        self._scopes: dict[Source, _Scope] = {}
        self._resolved: dict[tuple[Source, int], Source | None] = {}
        self._parameters: dict[Source, tuple[str, ...]] = {}

    # -- the tree, as definitions -------------------------------------------

    def _index(
        self,
        module: str,
        node: ast.AST,
        quals: tuple[str, ...],
        owner: Source | None,
        holder: Source | None,
        body: Source | None,
    ) -> None:
        """Index *node*'s children, attributing each to what encloses it.

        *owner* is the class a definition is declared in, *holder* the
        function it is nested in, and *body* the function whose own body
        this node stands in — which a class body interrupts, because a
        method's statements are not its enclosing function's.  A function's
        decorators and its default values are its own, which is how a
        declaration written above a function is read as that function's.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTION_NODES):
                address = Source(module=module, function=".".join((*quals, child.name)))
                self.functions[address] = child
                self._parent[address] = holder
                self._calls[address] = []
                self._assigns[address] = []
                declared_here = owner is not None and owner.function == ".".join(quals)
                self._owner[address] = (
                    owner
                    if declared_here
                    else (self._owner.get(holder) if holder is not None else None)
                )
                self._index(
                    module, child, (*quals, child.name), owner, address, address
                )
            elif isinstance(child, ast.ClassDef):
                address = Source(module=module, function=".".join((*quals, child.name)))
                self.classes[address] = child
                self._index(module, child, (*quals, child.name), address, holder, None)
            else:
                if isinstance(child, ast.ImportFrom):
                    self._import(module, child)
                self._attribute(body, child)
                self._index(module, child, quals, owner, holder, body)

    def _attribute(self, body: Source | None, child: ast.AST) -> None:
        """Keep what a reader of *body* asks for: its calls, names and binds.

        A name mentioned at module or class level stands in no function
        body, and is kept with no holder: it is still a reference, and one
        no call in a function accounts for.
        """
        if isinstance(child, ast.Attribute):
            self._references.setdefault(child.attr, []).append((body, child))
        elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            self._references.setdefault(child.id, []).append((body, child))
        if body is None:
            return
        if isinstance(child, ast.Call):
            self._calls[body].append(child)
            self._calls_by_callee[id(child.func)] = child
        elif (
            isinstance(child, ast.Assign)
            and len(child.targets) == 1
            and isinstance(child.targets[0], ast.Name)
        ):
            self._assigns[body].append(child)

    def _import(self, module: str, node: ast.ImportFrom) -> None:
        if node.level != 0 or node.module is None:
            return
        if node.module.split(".")[0] != "kodezart":
            return
        for alias in node.names:
            self._imports[module][alias.asname or alias.name] = (
                node.module,
                alias.name,
            )

    def parent(self, holder: Source) -> Source | None:
        """The definition *holder* is nested inside, if it is nested."""
        return self._parent.get(holder)

    def owner(self, holder: Source) -> Source | None:
        """The class *holder* is declared in, closures of a method included."""
        return self._owner.get(holder)

    def direct_calls(self, holder: Source) -> Sequence[ast.Call]:
        """The calls this body makes itself, not those its nested defs make."""
        return self._calls[holder]

    # -- names ---------------------------------------------------------------

    def nested(self, holder: Source, name: str) -> Source | None:
        """The function *name* names where *holder* stands, if it is local."""
        scope: Source | None = holder
        while scope is not None:
            candidate = Source(module=scope.module, function=f"{scope.function}.{name}")
            if candidate in self.functions:
                return candidate
            scope = self._parent.get(scope)
        return None

    def declared(
        self, module: str, name: str, seen: Sequence[Source] = ()
    ) -> Source | None:
        """The module-level definition *name* names in *module*, or its import."""
        address = Source(module=module, function=name)
        if address in self.classes or address in self.functions:
            return address
        imported = self._imports.get(module, {}).get(name)
        if imported is None:
            return None
        target = self._module_of.get(imported[0])
        if target is None or address in seen:
            return None
        return self.declared(target, imported[1], (*seen, address))

    def visible(self, holder: Source, name: str) -> Source | None:
        """What a bare name names where *holder* stands: local, then module."""
        return self.nested(holder, name) or self.declared(holder.module, name)

    def member(self, owner: Source | None, name: str, depth: int = 0) -> Source | None:
        """The method *name* on *owner*, its own body before its bases."""
        if owner is None or depth > BASE_DEPTH:
            return None
        address = Source(module=owner.module, function=f"{owner.function}.{name}")
        if address in self.functions:
            return address
        for base in self.classes[owner].bases:
            found = self.member(self.annotated(owner.module, base), name, depth + 1)
            if found is not None:
                return found
        return None

    # -- types ---------------------------------------------------------------

    def annotated(self, module: str, annotation: ast.expr) -> Source | None:
        """The class an annotation names, a string annotation included."""
        if isinstance(annotation, ast.Name):
            address = self.declared(module, annotation.id)
            return address if address in self.classes else None
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return None
            return self.annotated(module, parsed)
        return None

    def attribute_type(self, owner: Source | None, name: str) -> Source | None:
        """The class ``self.<name>`` holds in *owner*, when the source says so.

        A class annotation states it.  So does an ``__init__`` that assigns
        a constructor call or an annotated parameter — tuple targets paired
        element by element, which is how a writer assigning its port and its
        verifier on one line is read.  Two readings that disagree, or one
        assignment nothing types, leaves the attribute untyped.
        """
        if owner is None:
            return None
        key = (owner, name)
        if key not in self._attribute_types:
            self._attribute_types[key] = self._attribute_type(owner, name)
        return self._attribute_types[key]

    def _attribute_type(self, owner: Source, name: str) -> Source | None:
        module = owner.module
        found: set[Source | None] = set()
        for item in self.classes[owner].body:
            if (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id == name
            ):
                found.add(self.annotated(module, item.annotation))
        initializer = self.functions.get(
            Source(module=module, function=f"{owner.function}.__init__")
        )
        if initializer is not None:
            declared = {
                argument.arg: self.annotated(module, argument.annotation)
                for argument in (*initializer.args.args, *initializer.args.kwonlyargs)
                if argument.annotation is not None
            }
            for statement in ast.walk(initializer):
                if not isinstance(statement, ast.Assign):
                    continue
                for target in statement.targets:
                    for part, value in paired(target, statement.value):
                        if is_own_attribute(part, name):
                            found.add(self._constructed(module, value, declared))
        return next(iter(found)) if len(found) == 1 else None

    def _constructed(
        self,
        module: str,
        value: ast.expr,
        declared: Mapping[str, Source | None],
    ) -> Source | None:
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            address = self.declared(module, value.func.id)
            return address if address in self.classes else None
        if isinstance(value, ast.Name):
            return declared.get(value.id)
        return None

    def _scope(self, holder: Source) -> "_Scope":
        if holder not in self._scopes:
            self._scopes[holder] = self._build_scope(holder)
        return self._scopes[holder]

    def _build_scope(self, holder: Source) -> "_Scope":
        node = self.functions[holder]
        module = holder.module
        names: dict[str, Source | None] = {}
        taken = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in taken:
            if argument.annotation is not None:
                names[argument.arg] = self.annotated(module, argument.annotation)
        positional = (*node.args.posonlyargs, *node.args.args)
        if positional and positional[0].arg in OWN_RECEIVERS:
            names[positional[0].arg] = self._owner.get(holder)
        bindings: dict[str, list[ast.expr]] = {}
        for statement in self._assigns[holder]:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append(statement.value)
        for name, values in bindings.items():
            constructed = {self._constructed(module, value, {}) for value in values}
            names[name] = next(iter(constructed)) if len(constructed) == 1 else None
        return _Scope(
            names=names,
            bindings=bindings,
            parameters=frozenset(argument.arg for argument in taken),
        )

    def bindings(self, holder: Source) -> Mapping[str, Sequence[ast.expr]]:
        """Every local name *holder* assigns, with what it was assigned."""
        return self._scope(holder).bindings

    def parameter_names(self, holder: Source) -> frozenset[str]:
        """Every parameter *holder* takes, under whatever calling shape."""
        return frozenset(self._scope(holder).parameters)

    def _named_type(self, holder: Source, name: str) -> Source | None:
        scope: Source | None = holder
        while scope is not None:
            names = self._scope(scope).names
            if name in names:
                return names[name]
            scope = self._parent.get(scope)
        return None

    def type_of(self, holder: Source, expression: ast.expr) -> Source | None:
        """The class *expression* holds where *holder* stands, if declared."""
        if isinstance(expression, ast.Name):
            return self._named_type(holder, expression.id)
        if (
            isinstance(expression, ast.Attribute)
            and isinstance(expression.value, ast.Name)
            and expression.value.id in OWN_RECEIVERS
        ):
            return self.attribute_type(
                self._named_type(holder, expression.value.id), expression.attr
            )
        if isinstance(expression, ast.Call):
            resolved = self.resolve(holder, expression)
            return resolved if resolved in self.classes else None
        return None

    # -- calls ---------------------------------------------------------------

    def resolve(self, holder: Source, call: ast.Call) -> Source | None:
        """What *call* calls: a function, or a class for a constructor."""
        key = (holder, id(call))
        if key not in self._resolved:
            self._resolved[key] = self._resolve(holder, call)
        return self._resolved[key]

    def _resolve(self, holder: Source, call: ast.Call) -> Source | None:
        callee = call.func
        if isinstance(callee, ast.Name):
            return self.visible(holder, callee.id)
        if isinstance(callee, ast.Attribute):
            return self.member(self.type_of(holder, callee.value), callee.attr)
        return None

    def parameters(self, target: Source) -> tuple[str, ...]:
        """The positional parameters *target* takes, a constructor's included."""
        if target not in self._parameters:
            self._parameters[target] = self._parameters_of(target)
        return self._parameters[target]

    def _parameters_of(self, target: Source) -> tuple[str, ...]:
        node = self.functions.get(target)
        if node is not None:
            names = [
                argument.arg for argument in (*node.args.posonlyargs, *node.args.args)
            ]
            if names and names[0] in OWN_RECEIVERS:
                names = names[1:]
            return tuple(names)
        initializer = self.functions.get(
            Source(module=target.module, function=f"{target.function}.__init__")
        )
        if initializer is not None:
            return tuple(
                argument.arg
                for argument in (*initializer.args.posonlyargs, *initializer.args.args)
            )[1:]
        return tuple(
            item.target.id
            for item in self.classes[target].body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        )

    def arguments(
        self, target: Source, call: ast.Call
    ) -> tuple[tuple[str | None, ast.expr], ...]:
        """What *call* hands *target*, each argument under its parameter."""
        names = self.parameters(target)
        bound: list[tuple[str | None, ast.expr]] = [
            (names[position] if position < len(names) else None, argument)
            for position, argument in enumerate(call.args)
        ]
        bound.extend((word.arg, word.value) for word in call.keywords)
        return tuple(bound)

    # -- references ----------------------------------------------------------

    def references(self, name: str) -> Sequence[tuple[Source | None, ast.expr]]:
        """Every mention of *name* in the tree, and the body it stands in.

        A mention at module or class level stands in no body and has no
        holder.
        """
        return self._references.get(name, ())

    def call_of(self, reference: ast.expr) -> ast.Call | None:
        """The call *reference* is the callee of, if it is one."""
        return self._calls_by_callee.get(id(reference))


class _Scope:
    """What a function body's names stand for, as far as the source says."""

    __slots__ = ("bindings", "names", "parameters")

    def __init__(
        self,
        *,
        names: Mapping[str, Source | None],
        bindings: Mapping[str, Sequence[ast.expr]],
        parameters: frozenset[str],
    ) -> None:
        self.names = names
        self.bindings = bindings
        self.parameters = parameters


def paired(target: ast.expr, value: ast.expr) -> tuple[tuple[ast.expr, ast.expr], ...]:
    """An assignment's targets beside its values, element by element."""
    if (
        isinstance(target, ast.Tuple)
        and isinstance(value, ast.Tuple)
        and len(target.elts) == len(value.elts)
    ):
        return tuple(zip(target.elts, value.elts, strict=True))
    return ((target, value),)


def is_own_attribute(target: ast.expr, name: str) -> bool:
    """Whether *target* assigns ``self.<name>`` on the class's own receiver."""
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id in OWN_RECEIVERS
        and target.attr == name
    )
