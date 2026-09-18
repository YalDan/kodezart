#!/usr/bin/env python3.12
"""Derive the real sym_kind of every symbol of a file, from the very models
cut_views_r builds (py_model / toml_model / md_model / env_model).

Vocabulary (the one the maps already use):
  import, function, method, class_attr, assignment, class,
  toml_key, toml_table, env_key, md_h2, md_h3

The models themselves carry the discriminators:
  py    : key prefix "import:", entry["is_class"], entry["cls"] (container), and
          the model's own AST ("tree") for def-vs-assignment
  toml  : key prefix "table:" (and the [[array]] headers, stored unprefixed)
  md    : entry["level"]  (2 -> md_h2, 3 -> md_h3)
  env   : every entry is a key
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut_views_r as C  # noqa: E402


def _py_kinds(src: str) -> dict[str, str]:
    """Walk exactly the way C.py_model walks, recording each entry's kind."""
    lines = src.splitlines()
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out[C._import_key(lines, node)] = "import"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "function"
        elif isinstance(node, ast.ClassDef):
            out[node.name] = "class"
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = "method"
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            out[f"{node.name}.{t.id}"] = "class_attr"
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    out[f"{node.name}.{sub.target.id}"] = "class_attr"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = "assignment"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = "assignment"
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            out[node.name.id] = "assignment"      # PEP 695 `type X = ...`
    return out


def _toml_kinds(src: str) -> dict[str, str]:
    m = C.toml_model(src)
    arrays = set()
    for raw in m["lines"]:
        hm = C._HDR.match(raw)
        if hm and not raw.strip().startswith("#") and hm.group(1) == "[[":
            arrays.add(hm.group(2).strip())
    out = {}
    for k in m["sym"]:
        if k.startswith("table:") or k in arrays:
            out[k] = "toml_table"
        else:
            out[k] = "toml_key"
    return out


def _md_kinds(src: str) -> dict[str, str]:
    m = C.md_model(src)
    return {k: ("md_h2" if v.get("level") == 2 else "md_h3") for k, v in m["sym"].items()}


def _env_kinds(src: str) -> dict[str, str]:
    return {k: "env_key" for k in C.env_model(src)["sym"]}


def kinds_for(path: str, src: str) -> dict[str, str]:
    kind = C.model_for(path)
    if kind == "py":
        return _py_kinds(src)
    if kind is C.toml_model:
        return _toml_kinds(src)
    if kind is C.md_model:
        return _md_kinds(src)
    return _env_kinds(src)


def kinds_at(rev: str, path: str) -> dict[str, str]:
    src = C.blob(rev, path)
    if src is None:
        return {}
    try:
        return kinds_for(path, src)
    except SyntaxError:
        return {}


def kind_from_shape(path: str, key: str) -> str:
    """Last resort: the name's own shape."""
    if key.startswith("import:"):
        return "import"
    if path.endswith(".py"):
        return "method" if "." in key else "function"
    if path.endswith(".toml"):
        if key.startswith("table:"):
            return "toml_table"
        return "toml_key" if "." in key else "toml_table"
    if path.endswith(".md"):
        if key.startswith("H3:"):
            return "md_h3"
        return "md_h2"
    return "env_key"
