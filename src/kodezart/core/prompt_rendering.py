"""The single prompt rendering path.

Exactly three constructs, no templating dependency:

- ``{{name}}`` — name substitution, dotted paths supported.
- ``{{#each seq}}...{{/each}}`` — iteration, exposing ``{{this}}`` for the
  current item and ``{{@index1}}`` for its 1-based position.
- ``{{#if name}}...{{/if}}`` — presence conditional; a name bound to
  ``None`` counts as absent.

Every UNCONDITIONAL reference with no binding is collected and reported in
one :class:`PromptRenderError`.  A reference that appears only inside a
false ``{{#if}}`` block is a legal runtime state and is never reported.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from kodezart.core.errors import PromptRenderError
from kodezart.types.domain.prompts import PromptKey

_TAG: Final[re.Pattern[str]] = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_ITEM_NAME: Final[str] = "this"
_INDEX_NAME: Final[str] = "@index1"
_IF: Final[str] = "if"
_EACH: Final[str] = "each"

_ABSENT: Final[object] = object()


@dataclass(frozen=True)
class _Text:
    """Literal template text."""

    text: str


@dataclass(frozen=True)
class _Name:
    """A ``{{name}}`` reference."""

    path: str


@dataclass(frozen=True)
class _Block:
    """An ``{{#if}}`` or ``{{#each}}`` block."""

    kind: str
    path: str
    body: tuple["_Text | _Name | _Block", ...]


_Node = _Text | _Name | _Block


@dataclass(frozen=True)
class _Scope:
    """One resolution frame: explicit names plus an optional current item."""

    names: Mapping[str, object]
    item: object


def _parse(body: str) -> tuple[_Node, ...]:
    """Parse *body* into a node tree. Raises on unknown or unbalanced tags."""
    stack: list[tuple[str, str, list[_Node]]] = [("", "", [])]
    pos = 0
    for match in _TAG.finditer(body):
        if match.start() > pos:
            stack[-1][2].append(_Text(text=body[pos : match.start()]))
        pos = match.end()
        tag = match.group(1).strip()
        if tag.startswith("#"):
            kind, _, path = tag[1:].partition(" ")
            if kind not in (_IF, _EACH) or not path.strip():
                msg = f"Unsupported block tag {{{{{tag}}}}}"
                raise PromptRenderError(msg)
            stack.append((kind, path.strip(), []))
        elif tag.startswith("/"):
            kind = tag[1:]
            if len(stack) == 1 or stack[-1][0] != kind:
                msg = f"Unbalanced closing tag {{{{{tag}}}}}"
                raise PromptRenderError(msg)
            open_kind, open_path, children = stack.pop()
            stack[-1][2].append(
                _Block(kind=open_kind, path=open_path, body=tuple(children)),
            )
        else:
            stack[-1][2].append(_Name(path=tag))
    if pos < len(body):
        stack[-1][2].append(_Text(text=body[pos:]))
    if len(stack) != 1:
        msg = f"Unclosed {{{{#{stack[-1][0]}}}}} block in template"
        raise PromptRenderError(msg)
    return tuple(stack[0][2])


def _member(container: object, name: str) -> object:
    """Read *name* off a mapping or an attribute holder; ``_ABSENT`` if absent."""
    if isinstance(container, Mapping):
        mapping: Mapping[str, object] = container
        if name in mapping:
            return mapping[name]
        return _ABSENT
    value: object = getattr(container, name, _ABSENT)
    return value


def _resolve(path: str, scopes: Sequence[_Scope]) -> object:
    """Resolve a dotted *path*, innermost scope first. ``_ABSENT`` if unbound."""
    head, _, rest = path.partition(".")
    current: object = _ABSENT
    for scope in reversed(scopes):
        if head in scope.names:
            current = scope.names[head]
            break
        if scope.item is not _ABSENT:
            candidate = _member(scope.item, head)
            if candidate is not _ABSENT:
                current = candidate
                break
    if current is _ABSENT:
        return _ABSENT
    for part in rest.split(".") if rest else []:
        current = _member(current, part)
        if current is _ABSENT:
            return _ABSENT
    return current


def _render_nodes(
    nodes: Sequence[_Node],
    scopes: list[_Scope],
    missing: list[str],
) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, _Text):
            out.append(node.text)
        elif isinstance(node, _Name):
            value = _resolve(node.path, scopes)
            if value is _ABSENT or value is None:
                if node.path not in missing:
                    missing.append(node.path)
                continue
            out.append(str(value))
        elif node.kind == _IF:
            value = _resolve(node.path, scopes)
            if value is not _ABSENT and value is not None:
                out.append(_render_nodes(node.body, scopes, missing))
        else:
            value = _resolve(node.path, scopes)
            if value is _ABSENT or value is None:
                if node.path not in missing:
                    missing.append(node.path)
                continue
            if not isinstance(value, list | tuple):
                msg = f"{{{{#each {node.path}}}}} requires a list, got {type(value)}"
                raise PromptRenderError(msg)
            items: Sequence[object] = value
            for index, item in enumerate(items, start=1):
                frame: dict[str, object] = {_INDEX_NAME: index, _ITEM_NAME: item}
                scopes.append(_Scope(names=frame, item=item))
                out.append(_render_nodes(node.body, scopes, missing))
                scopes.pop()
    return "".join(out)


def render_template(body: str, bindings: Mapping[str, object]) -> str:
    """Render *body* against *bindings* — the one substitution implementation.

    Raises :class:`PromptRenderError` naming every unconditional reference
    that had no binding.
    """
    nodes = _parse(body)
    missing: list[str] = []
    scopes: list[_Scope] = [_Scope(names=bindings, item=_ABSENT)]
    rendered = _render_nodes(nodes, scopes, missing)
    if missing:
        msg = "Unbound template placeholders: " + ", ".join(missing)
        raise PromptRenderError(msg, missing=missing)
    return rendered


@dataclass(frozen=True)
class PromptTemplate:
    """An unrendered template plus the binding sources its set contributed.

    ``bindings`` carries the set-level fragments and (once KOD-50 registers
    it) the operation-config namespace.  Per-call typed variables are passed
    to :meth:`render`.  Rendering itself is delegated to
    :func:`render_template` — this type performs no substitution of its own.
    """

    key: PromptKey
    source: str
    body: str
    bindings: Mapping[str, object]

    def render(self, variables: Mapping[str, object]) -> str:
        """Render this template with *variables* layered over its bindings."""
        merged: dict[str, object] = {**self.bindings, **variables}
        return render_template(self.body, merged)


def binding_names(body: str) -> frozenset[str]:
    """Every distinct name referenced by *body*, blocks included."""
    names: set[str] = set()
    _collect(_parse(body), names)
    return frozenset(names)


def _collect(nodes: Sequence[_Node], names: set[str]) -> None:
    for node in nodes:
        if isinstance(node, _Text):
            continue
        if isinstance(node, _Name):
            if node.path not in (_ITEM_NAME, _INDEX_NAME):
                names.add(node.path)
            continue
        names.add(node.path)
        _collect(node.body, names)


def free_binding_names(body: str) -> frozenset[str]:
    """Every name *body* must resolve from its bindings — loop-locals excluded.

    Inside an ``{{#each}}`` frame, a reference rooted at the current item or
    its index is a member of the iterated value, not a binding: the block's
    own path is what the bindings have to supply.  ``{{#each repos}}`` with
    ``{{this.url}}`` in its body therefore has one free name, ``repos``.
    """
    names: set[str] = set()
    _collect_free(_parse(body), names, in_each=False)
    return frozenset(names)


def _collect_free(nodes: Sequence[_Node], names: set[str], *, in_each: bool) -> None:
    for node in nodes:
        if isinstance(node, _Text):
            continue
        if not _is_loop_local(node.path, in_each=in_each):
            names.add(node.path)
        if isinstance(node, _Block):
            _collect_free(node.body, names, in_each=in_each or node.kind == _EACH)


def _is_loop_local(path: str, *, in_each: bool) -> bool:
    """Whether *path* is rooted at a name an enclosing each-frame supplies."""
    return in_each and path.partition(".")[0] in (_ITEM_NAME, _INDEX_NAME)
