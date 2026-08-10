"""The composition root is a call graph, not a definition site.

`main.py` grew from 151 lines and two definitions at `92597c0` to 619 and
nine, one lane at a time, because every lane that ships an adapter has a
reason to add its builder here.  No criterion could see it: each addition
was locally correct and the file is not named by any Verification section.

That growth is not only untidiness.  The composition root is a measured
collision point — two parallel lanes were found to collide in seventeen
files, the engine construction here among them — so every builder defined
rather than imported is a merge conflict waiting for the next lane.

The rule is therefore mechanical, and this is the guard: the composition
root may define its framework hook and its factory, and nothing else.
A builder belongs in `kodezart.composition`, where it is unit-testable
without importing the application.
"""

import ast
from pathlib import Path

#: The two definitions the composition root is allowed to own: the ASGI
#: lifespan hook the framework calls, and the application factory.
PERMITTED: frozenset[str] = frozenset({"lifespan", "create_app"})

ROOT: Path = Path(__file__).resolve().parents[1] / "src" / "kodezart" / "main.py"


def _top_level_definitions() -> list[str]:
    """Every function, coroutine and class defined at module level."""
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]


def test_the_composition_root_defines_only_its_hook_and_its_factory() -> None:
    """A builder defined here is one no test can reach without the app."""
    surplus = sorted(set(_top_level_definitions()) - PERMITTED)

    assert surplus == [], (
        f"{ROOT.name} defines {surplus}, which belong in kodezart.composition. "
        "The composition root imports and wires; it does not define."
    )


def test_the_guard_reads_a_real_module() -> None:
    """An empty parse would let the rule above pass over anything."""
    defined = _top_level_definitions()

    assert set(defined) == PERMITTED
