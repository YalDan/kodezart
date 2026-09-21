"""The shared resolver finds a name under every spelling a module can give it."""

import ast

import pytest

from tests.name_resolution import (
    SOURCE_ROOT,
    annotated_parameters,
    bound_names,
    call_sites,
    definitions,
    parameters_receiving,
    parsed,
    reaches,
    resolve,
    source_tree,
)

GAP = "compute_gap"
DIGEST_HOME = "kodezart.domain.fire_spec"


def tree(source):
    return ast.parse(source)


def test_an_aliased_from_import_denotes_the_imported_name():
    aliased = tree(
        "from kodezart.domain.gap import compute_gap as gap_of\n"
        "\n"
        "def plan(rows):\n"
        "    return gap_of(rows)\n"
    )
    resolution = resolve(aliased, names={GAP})

    assert resolution.names["gap_of"] == GAP
    assert reaches(aliased, names={GAP}) == frozenset({GAP})

    plain = tree(
        "from kodezart.domain.gap import compute_gap\n"
        "\n"
        "def plan(rows):\n"
        "    return compute_gap(rows)\n"
    )
    assert reaches(plain, names={GAP}) == frozenset({GAP})
    assert reaches(tree("def plan(rows):\n    return sorted(rows)\n"), names={GAP}) == (
        frozenset()
    )


@pytest.mark.parametrize(
    ("route", "source"),
    [
        (
            "aliased_module",
            "import kodezart.domain.gap as m\n"
            "\n"
            "def plan(rows):\n"
            "    return m.compute_gap(rows)\n",
        ),
        (
            "dotted_module",
            "import kodezart.domain.gap\n"
            "\n"
            "def plan(rows):\n"
            "    return kodezart.domain.gap.compute_gap(rows)\n",
        ),
    ],
)
def test_a_module_imported_under_another_name_routes_its_attributes(route, source):
    routed = tree(source)
    resolution = resolve(routed, names={GAP})
    call = next(node for node in ast.walk(routed) if isinstance(node, ast.Call))

    assert resolution.denotes(call.func) == GAP
    assert reaches(routed, names={GAP}) == frozenset({GAP})

    unrelated = tree(source.replace("compute_gap", "sorted_rows"))
    assert (
        resolve(unrelated, names={GAP}).denotes(
            next(
                node for node in ast.walk(unrelated) if isinstance(node, ast.Call)
            ).func
        )
        is None
    )


def test_an_assignment_alias_denotes_the_name_to_a_fixed_point():
    aliased = tree(
        "from kodezart.domain.gap import compute_gap\n"
        "\n"
        "def plan(rows):\n"
        "    return later(rows)\n"
        "\n"
        "later = earlier\n"
        "earlier = compute_gap\n"
    )
    resolution = resolve(aliased, names={GAP})

    assert resolution.names["earlier"] == GAP
    assert resolution.names["later"] == GAP
    assert reaches(aliased, names={GAP}) == frozenset({GAP})

    unbound = tree("def plan(rows):\n    return later(rows)\n\nlater = earlier\n")
    assert "later" not in resolve(unbound, names={GAP}).names


def test_a_local_definition_is_not_the_import_when_the_home_module_is_named():
    planted = tree(
        "def body_digest(body):\n"
        "    return body\n"
        "\n"
        "def pin(spec):\n"
        "    return body_digest(spec.body)\n"
    )

    assert resolve(planted, names={"body_digest"}, from_module=DIGEST_HOME).names == {}
    assert resolve(planted, names={"body_digest"}).names["body_digest"] == "body_digest"

    imported = tree(
        f"from {DIGEST_HOME} import body_digest as pin_of\n"
        "\n"
        "def pin(spec):\n"
        "    return pin_of(spec.body)\n"
    )
    homed = resolve(imported, names={"body_digest"}, from_module=DIGEST_HOME)
    assert homed.names == {"pin_of": "body_digest"}


def test_a_string_constant_is_not_a_route():
    vocabulary = tree('IN_GAP = "in_gap"\n\nTERMINALS = (IN_GAP,)\n')

    assert reaches(vocabulary, names={"in_gap"}) == frozenset()
    assert reaches(tree("def in_gap(rows):\n    return rows\n"), names={"in_gap"}) == (
        frozenset({"in_gap"})
    )


@pytest.mark.parametrize(
    ("form", "source", "expected"),
    [
        (
            "plain",
            "from kodezart.domain.gap import compute_gap\n"
            "\n"
            "def plan(rows):\n"
            "    return compute_gap(rows)\n",
            1,
        ),
        (
            "aliased",
            "from kodezart.domain.gap import compute_gap as gap_of\n"
            "\n"
            "def plan(rows):\n"
            "    return gap_of(rows)\n",
            1,
        ),
        (
            "module_attribute",
            "import kodezart.domain.gap as m\n"
            "\n"
            "def plan(rows):\n"
            "    return m.compute_gap(rows)\n",
            1,
        ),
        (
            "assigned_alias",
            "from kodezart.domain.gap import compute_gap\n"
            "\n"
            "gap_of = compute_gap\n"
            "\n"
            "def plan(rows):\n"
            "    return gap_of(rows)\n",
            1,
        ),
        (
            "import_alone",
            "from kodezart.domain.gap import compute_gap\n",
            0,
        ),
        (
            "unrelated_call",
            "from kodezart.domain.gap import compute_gap\n"
            "\n"
            "def plan(rows):\n"
            "    return sorted(rows)\n",
            0,
        ),
    ],
)
def test_a_call_site_is_found_under_every_spelling_the_call_can_take(
    form, source, expected
):
    found = call_sites({"services/planted.py": tree(source)}, names={GAP})

    assert len(found) == expected
    assert all(site.module == "services/planted.py" for site in found)
    assert all(site.name == GAP for site in found)
    assert all(site.definition == "plan" for site in found)


def test_a_method_call_on_an_aliased_class_is_found():
    source = (
        "from kodezart.types.domain.agent import TicketDraftOutput as Draft\n"
        "\n"
        "def parse(raw):\n"
        "    return Draft.model_validate(raw)\n"
    )
    found = call_sites(
        {"chains/planted.py": tree(source)},
        names={"TicketDraftOutput"},
        methods={"model_validate"},
    )

    assert [(site.name, site.definition) for site in found] == [
        ("TicketDraftOutput", "parse")
    ]
    assert (
        call_sites(
            {
                "chains/planted.py": tree(
                    source.replace("Draft.model_validate", "loads")
                )
            },
            names={"TicketDraftOutput"},
            methods={"model_validate"},
        )
        == ()
    )


def makes_a_spec(value, names):
    if isinstance(value, ast.Name):
        return value.id in names
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "current_fire_spec"
    )


@pytest.mark.parametrize(
    ("form", "body"),
    [
        ("assign", "    spec = current_fire_spec(state)\n"),
        ("annotated_assign", "    spec: object = current_fire_spec(state)\n"),
        ("walrus", "    if (spec := current_fire_spec(state)):\n        pass\n"),
        ("for_target", "    for spec in current_fire_spec(state):\n        pass\n"),
        ("with_as", "    with current_fire_spec(state) as spec:\n        pass\n"),
    ],
)
def test_a_name_bound_by_each_binding_form_is_found(form, body):
    held = tree(f"def run(state):\n{body}")
    assert bound_names(held, yields=makes_a_spec) == frozenset({"spec"})

    other = tree(f"def run(state):\n{body.replace('current_fire_spec', 'unrelated')}")
    assert bound_names(other, yields=makes_a_spec) == frozenset()


def test_an_annotated_parameter_is_a_seed():
    annotated = tree(
        "def own_text(spec: TrackerSpec, raw: str) -> str:\n    return spec.body\n"
    )

    assert annotated_parameters(annotated, mentioning={"TrackerSpec"}) == frozenset(
        {"spec"}
    )
    assert annotated_parameters(annotated, mentioning={"AuthoredSpec"}) == frozenset()

    quoted = tree('def own_text(spec: "TrackerSpec") -> str:\n    return ""\n')
    assert annotated_parameters(quoted, mentioning={"TrackerSpec"}) == frozenset(
        {"spec"}
    )


def hands_a_spec(module, argument):
    return isinstance(argument, ast.Name) and argument.id == "spec"


CALLER = (
    "{IMPORT}\n"
    "\n"
    "def run(state):\n"
    "    spec = current_fire_spec(state)\n"
    "    return {CALL}\n"
)
OWN_TEXT_IMPORT = "from kodezart.b import _own_text"
PROBE = (
    "def _own_text(value):\n"
    "    return (\n"
    "        value.body\n"
    "        if isinstance(value, TrackerSpec)\n"
    "        else format_fire_spec(value)\n"
    "    )\n"
)


def caller(imported, call):
    """The caller module, spelling its import and its call as given."""
    return CALLER.replace("{IMPORT}", imported).replace("{CALL}", call)


@pytest.mark.parametrize(
    ("form", "imported", "call", "probe"),
    [
        ("positional", OWN_TEXT_IMPORT, "_own_text(spec)", PROBE),
        ("keyword", OWN_TEXT_IMPORT, "_own_text(value=spec)", PROBE),
        (
            "through_a_receiver",
            OWN_TEXT_IMPORT,
            "self._own_text(spec)",
            "class Reader:\n"
            "    def _own_text(self, value):\n"
            "        return value.body\n",
        ),
        ("aliased_import", "from kodezart.b import _own_text as h", "h(spec)", PROBE),
    ],
)
def test_an_unannotated_parameter_handed_a_bound_value_at_a_call_is_a_seed(
    form, imported, call, probe
):
    trees = parsed({"a.py": caller(imported, call), "b.py": probe})

    assert parameters_receiving(trees, yields=hands_a_spec) == {
        ("b.py", "_own_text"): frozenset({"value"})
    }

    unrelated = parsed(
        {"a.py": caller(imported, call.replace("spec", "state")), "b.py": probe}
    )
    assert parameters_receiving(unrelated, yields=hands_a_spec) == {}


@pytest.mark.parametrize(
    ("route", "imported", "call"),
    [
        ("module_alias", "import kodezart.b as helpers", "helpers._own_text(spec)"),
        ("submodule_import", "from kodezart import b", "b._own_text(spec)"),
    ],
)
def test_a_callee_reached_through_a_module_route_is_that_modules_own_definition(
    route, imported, call
):
    """A module receiver fills no parameter, so the first one takes the value."""
    trees = parsed({"a.py": caller(imported, call), "b.py": PROBE})

    assert parameters_receiving(trees, yields=hands_a_spec) == {
        ("b.py", "_own_text"): frozenset({"value"})
    }


def test_a_receiver_that_spells_no_module_is_every_method_of_that_name():
    """An offset stays for a real receiver: ``self`` is filled, not handed."""
    trees = parsed(
        {
            "a.py": caller("from kodezart.b import Reader", "Reader()._own_text(spec)"),
            "b.py": "class Reader:\n"
            "    def _own_text(self, value):\n"
            "        return value.body\n",
        }
    )

    assert parameters_receiving(trees, yields=hands_a_spec) == {
        ("b.py", "_own_text"): frozenset({"value"})
    }


def test_a_callee_is_resolved_to_the_callers_own_definition_first():
    trees = parsed(
        {
            "a.py": "from kodezart.b import helper\n"
            "\n"
            "def helper(here):\n"
            "    return here\n"
            "\n"
            "def run(spec):\n"
            "    return helper(spec)\n",
            "b.py": "def helper(elsewhere):\n    return elsewhere\n",
        }
    )

    assert parameters_receiving(trees, yields=hands_a_spec) == {
        ("a.py", "helper"): frozenset({"here"})
    }


def test_the_definition_a_node_sits_inside_is_named_in_full():
    module = tree("TOP = 1\n\nclass Reader:\n    def read(self):\n        return TOP\n")
    where = definitions(module)
    inner = next(node for node in ast.walk(module) if isinstance(node, ast.Return))
    top = next(node for node in ast.walk(module) if isinstance(node, ast.Assign))

    assert where[id(inner)] == "Reader.read"
    assert where[id(top)] == "<module>"


def test_the_source_tree_is_the_package_read_off_disk(tmp_path):
    tree_of_package = source_tree()

    assert SOURCE_ROOT.name == "kodezart"
    assert "domain/ticket.py" in tree_of_package
    assert all(relative.endswith(".py") for relative in tree_of_package)
    assert list(tree_of_package) == sorted(tree_of_package)

    (tmp_path / "inner").mkdir()
    (tmp_path / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    (tmp_path / "inner" / "two.py").write_text("TWO = 2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("not a module\n", encoding="utf-8")

    assert source_tree(tmp_path) == {"inner/two.py": "TWO = 2\n", "one.py": "ONE = 1\n"}
