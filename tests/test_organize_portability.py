"""Tracker phase-label spellings belong to operation configuration."""

import ast
import re
import sys
import tomllib
from itertools import product
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"
CONFIG_MODEL = Path("types/domain/operation.py")
LABEL = re.compile(r"(?<![\w-])(?:scope:[\w-]+|queue:approved:(?:ticket|criteria))\b")


LABEL_PREFIX = re.compile(r"(?:scope:|queue:approved:)(?=$|\{\{)")


def _labels(value):
    return set(LABEL.findall(value)) | set(LABEL_PREFIX.findall(value))


def _data_labels(value):
    if isinstance(value, str):
        return _labels(value)
    if isinstance(value, dict):
        return set().union(*(_data_labels(item) for item in (*value, *value.values())))
    if isinstance(value, list):
        return set().union(*(_data_labels(item) for item in value))
    return set()


def _combinations(nodes, bindings, seen):
    return product(*(_strings(node, bindings, seen) for node in nodes))


def _strings(node, bindings, seen=frozenset()):
    """Fold finite string expressions without importing or executing source."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and node.id not in seen:
        return set().union(
            *(
                _strings(value, bindings, seen | {node.id})
                for value in bindings.get(node.id, ())
            )
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return {
            left + right
            for left, right in _combinations((node.left, node.right), bindings, seen)
        }
    if isinstance(node, ast.JoinedStr):
        pieces = [
            part.value if isinstance(part, ast.FormattedValue) else part
            for part in node.values
        ]
        return {"".join(values) for values in _combinations(pieces, bindings, seen)}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        arguments = (
            node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        )
        results = set()
        for template in _strings(node.left, bindings, seen):
            for values in _combinations(arguments, bindings, seen):
                try:
                    results.add(template % values)
                except (TypeError, ValueError):
                    continue
        return results
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        receiver = _strings(node.func.value, bindings, seen)
        if (
            node.func.attr == "join"
            and len(node.args) == 1
            and isinstance(node.args[0], (ast.List, ast.Tuple))
            and not node.keywords
        ):
            return {
                separator.join(values)
                for separator in receiver
                for values in _combinations(node.args[0].elts, bindings, seen)
            }
        if node.func.attr == "format" and all(k.arg is not None for k in node.keywords):
            arguments = [*node.args, *(k.value for k in node.keywords)]
            results = set()
            for template in receiver:
                for values in _combinations(arguments, bindings, seen):
                    try:
                        results.add(
                            template.format(
                                *values[: len(node.args)],
                                **dict(
                                    zip(
                                        (k.arg for k in node.keywords),
                                        values[len(node.args) :],
                                        strict=True,
                                    )
                                ),
                            )
                        )
                    except (AttributeError, IndexError, KeyError, ValueError):
                        continue
            return results
    return set()


def _scope_nodes(scope):
    """Walk one lexical scope; inner scopes get their own local bindings."""
    pending = list(scope.body)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pending.extend(
                child for child in ast.iter_child_nodes(node) if child not in node.body
            )
        else:
            pending.extend(ast.iter_child_nodes(node))


def _python_labels(scope, inherited=None):
    nodes = tuple(_scope_nodes(scope))
    local = {}
    for node in nodes:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, (ast.AnnAssign, ast.NamedExpr))
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and node.value is not None:
                local.setdefault(target.id, []).append(node.value)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for argument in (
            *scope.args.posonlyargs,
            *scope.args.args,
            *scope.args.kwonlyargs,
        ):
            local.setdefault(argument.arg, [])
    bindings = {**(inherited or {}), **local}
    found = set()
    for node in nodes:
        for value in _strings(node, bindings):
            found.update(_labels(value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.update(_python_labels(node, bindings))
    return found


def _violations(root):
    found = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".toml"}:
            continue
        relative = path.relative_to(root)
        if relative == CONFIG_MODEL:
            continue
        source = path.read_text()
        labels = (
            _python_labels(ast.parse(source))
            if path.suffix == ".py"
            else _labels(source)
        )
        if path.suffix == ".toml":
            labels.update(_data_labels(tomllib.loads(source)))
        if labels:
            found[relative.as_posix()] = sorted(labels)
    return found


def test_source_and_prompt_data_have_no_literal_phase_labels():
    assert _violations(SOURCE) == {}


@pytest.mark.parametrize(
    "expression",
    [
        '"scope:approved"',
        '"scope:triage"',
        '"scope:proposed"',
        '"queue:approved:ticket"',
        '"queue:approved:criteria"',
        '"scope:" "approved"',
        '"scope" + ":" + "approved"',
        "f\"scope:{'approved'}\"",
        '":".join(["scope", "approved"])',
        '"{}:{}".format("scope", "approved")',
        '"{prefix}:{state}".format(prefix="scope", state="approved")',
        '"%s:%s" % ("scope", "approved")',
    ],
)
def test_literal_constructions_cannot_hide_labels(expression):
    assert _python_labels(ast.parse(f"label = {expression}"))


def test_constant_aliases_are_followed_within_nested_scopes():
    source = """
PREFIX = "queue"
def label():
    middle = "approved"
    suffix = "criteria"
    return ":".join([PREFIX, middle, suffix])
"""
    assert _python_labels(ast.parse(source)) == {"queue:approved:criteria"}


def test_unknown_bindings_and_semantic_keys_are_not_backend_labels():
    source = """
PREFIX = "scope"
def label(PREFIX, config):
    return PREFIX + ":" + config.scope_labels["approved"]
"""
    assert _python_labels(ast.parse(source)) == set()
    assert _python_labels(ast.parse('key = "approved"')) == set()


def test_alias_cycles_end_without_executing_the_source():
    source = """
first = second
second = first
raise RuntimeError("source must never run")
"""
    assert _python_labels(ast.parse(source)) == set()


@pytest.mark.parametrize("suffix", [".py", ".md", ".toml"])
def test_tree_guard_reports_literal_sources_and_exempts_only_config_model(
    tmp_path, suffix
):
    configuration = tmp_path / CONFIG_MODEL
    configuration.parent.mkdir(parents=True)
    configuration.write_text('label = "scope:approved"')
    offender = tmp_path / f"another_configuration{suffix}"
    offender.write_text('label = "scope:approved"')
    assert _violations(tmp_path) == {offender.name: ["scope:approved"]}
    offender.unlink()
    assert _violations(tmp_path) == {}


def test_template_bindings_remain_configurable(tmp_path):
    prompt = tmp_path / "organize.md"
    prompt.write_text("Use {{scope_labels.approved}} and {{issue_labels.body_ready}}.")
    assert _violations(tmp_path) == {}


@pytest.mark.parametrize("prefix", ["scope:", "queue:approved:"])
def test_literal_namespace_plus_unknown_runtime_suffix_is_still_hardcoded(prefix):
    source = f"def label(member):\n    return {prefix!r} + member.value"
    assert _python_labels(ast.parse(source)) == {prefix}


def test_toml_escaped_string_values_are_decoded_before_checking(tmp_path):
    path = tmp_path / "set.toml"
    path.write_text(r'label = "scope:\u0061pproved"')
    assert _violations(tmp_path) == {"set.toml": ["scope:approved"]}


def test_literal_namespace_before_a_template_binding_is_hardcoded(tmp_path):
    path = tmp_path / "organize.md"
    path.write_text("Set scope:{{selected_label}} after verification.")
    assert _violations(tmp_path) == {"organize.md": ["scope:"]}


@pytest.mark.parametrize(
    "source",
    [
        'def fn(label="scope:approved"):\n    return label',
        'def fn(*, label="scope" + ":" + "approved"):\n    return label',
        '@wrap("scope:approved")\ndef fn():\n    pass',
        '@wrap(":".join(["scope", "approved"]))\ndef fn():\n    pass',
        'def fn(label: "scope:approved"):\n    pass',
        'def fn() -> "scope:approved":\n    pass',
        'class Label(factory("scope:approved")):\n    pass',
        'class Label(metaclass=factory("scope" + ":" + "approved")):\n    pass',
    ],
)
def test_function_and_class_headers_cannot_hide_literal_labels(source):
    assert _python_labels(ast.parse(source))


def test_real_source_gate_rejects_an_injected_offending_tree(tmp_path, monkeypatch):
    (tmp_path / "worker.py").write_text('label = "scope:approved"')
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path)
    with pytest.raises(AssertionError):
        test_source_and_prompt_data_have_no_literal_phase_labels()
