"""The shared resolver finds a name under every spelling a module can give it."""

import ast

import pytest

import kodezart.domain
from kodezart.domain import gap as gap_module
from kodezart.domain.gap import compute_gap
from tests.name_resolution import (
    SOURCE_ROOT,
    annotated_parameters,
    bound_names,
    call_sites,
    definitions,
    imported_modules,
    loaded_values,
    module_namespace,
    named_object,
    parameters_receiving,
    parsed,
    reaches,
    referencing_definitions,
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


#: The two spellings that route a receiver to a module of the tree.
MODULE_ROUTES = [
    ("module_alias", "import kodezart.b as helpers", "helpers._own_text(spec)"),
    ("submodule_import", "from kodezart import b", "b._own_text(spec)"),
]


@pytest.mark.parametrize(("route", "imported", "call"), MODULE_ROUTES)
def test_a_callee_reached_through_a_module_route_is_that_modules_own_definition(
    route, imported, call
):
    """A module receiver fills no parameter, so the first one takes the value."""
    trees = parsed({"a.py": caller(imported, call), "b.py": PROBE})

    assert parameters_receiving(trees, yields=hands_a_spec) == {
        ("b.py", "_own_text"): frozenset({"value"})
    }


@pytest.mark.parametrize(("route", "imported", "call"), MODULE_ROUTES)
def test_a_module_route_reaches_no_method_of_the_routed_module(route, imported, call):
    """A module route lands on what the module states, never inside a class.

    The same two routes over a module whose only ``_own_text`` is a method:
    the top-level map holds no definition of that name, so the call reaches
    none and no parameter receives the spec. Read off the module's every
    definition instead, the method would be the target, and since a module
    receiver fills no parameter its ``self`` would take the spec.
    """
    trees = parsed(
        {
            "a.py": caller(imported, call),
            "b.py": "class Holder:\n"
            "    def _own_text(self, value):\n"
            "        return value.body\n",
        }
    )

    assert parameters_receiving(trees, yields=hands_a_spec) == {}


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


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("kodezart.domain.gap:compute_gap", compute_gap),
        ("kodezart.domain.gap.compute_gap", compute_gap),
        ("kodezart.domain.gap", gap_module),
        ("compute_gap", None),
        ("kodezart.domain.gap:absent", None),
        ("kodezart.domain.absent.compute_gap", None),
        ("os.path:join", None),
        ("kodezart.domain.gap compute_gap", None),
        ("(Check|Evidence)", None),
    ],
)
def test_a_string_naming_an_object_resolves_to_the_object_itself(text, named):
    assert named_object(text) is named


def test_the_namespace_of_a_module_is_what_its_text_binds():
    """The package's own globals for its text on disk, a fresh run otherwise."""
    relative = "domain/gap.py"
    source = source_tree()[relative]

    assert module_namespace(relative, source) is vars(gap_module)

    changed = module_namespace(relative, source + "\nPLANTED = compute_gap\n")
    assert changed is not vars(gap_module)
    assert changed["PLANTED"] is changed["compute_gap"]
    assert changed["compute_gap"] is not compute_gap
    assert changed["__name__"] == gap_module.__name__


def test_a_word_that_only_spells_the_object_does_not_refer_to_it():
    """A reference is the object itself, never its word.

    The planted module defines a function of the same name, quotes the name,
    and calls its own function: nothing there is the arithmetic.
    """
    source = (
        "def compute_gap(rows):\n"
        "    return rows\n"
        "\n"
        "LABEL = 'compute_gap'\n"
        "\n"
        "def plan(rows):\n"
        "    return compute_gap(rows)\n"
    )
    namespace = module_namespace("services/planted.py", source)

    assert (
        referencing_definitions(
            "services/planted.py", ast.parse(source), namespace, wanted=(compute_gap,)
        )
        == ()
    )


def test_a_submodule_imported_from_its_package_binds_the_submodule(monkeypatch):
    """``from package import name`` binds the submodule the package lacks.

    With the attribute taken off the package, as it is before the submodule
    is first imported, the import itself still binds the submodule, and so
    does the resolution here.
    """
    monkeypatch.delattr(kodezart.domain, "gap")
    source = (
        "def plan(rows):\n"
        "    from kodezart.domain import gap\n"
        "\n"
        "    return gap.compute_gap(rows, supersession_refs={})\n"
    )

    assert [
        name
        for name, _node in referencing_definitions(
            "services/planted.py",
            ast.parse(source),
            module_namespace("services/planted.py", source),
            wanted=(compute_gap,),
        )
    ] == ["plan"]


def test_a_relative_import_in_a_package_init_resolves_against_the_package():
    """A package's ``__init__`` is its own package, not its parent's module.

    Imported inside the function, so the name is bound by the import alone
    and never by the module's globals.
    """
    relative = "domain/planted/__init__.py"
    source = (
        "def plan(rows):\n"
        "    from ..gap import compute_gap as window\n"
        "\n"
        "    return window(rows, supersession_refs={})\n"
    )

    assert [
        name
        for name, _node in referencing_definitions(
            relative,
            ast.parse(source),
            module_namespace(relative, source),
            wanted=(compute_gap,),
        )
    ] == ["plan"]


@pytest.mark.parametrize(
    ("text", "edge"),
    [
        ("kodezart.domain.gap:in_gap", True),
        ("kodezart.domain.gap.in_gap", True),
        ("kodezart.domain:gap", True),
        ("kodezart.domain.gap", True),
        ("kodezart.domain.gap:absent", False),
        ("in_gap", False),
    ],
)
def test_a_string_naming_an_object_is_an_import_edge_to_its_module(text, edge):
    """A string ``pkgutil.resolve_name`` reads names the module it resolves in.

    The planted module imports nothing; the string alone is the edge, and a
    string that resolves to nothing is none.
    """
    sources = source_tree()
    sources["services/planted.py"] = f"TARGET = {text!r}\n"
    trees = parsed(sources)

    named = imported_modules("services/planted.py", trees["services/planted.py"], trees)

    assert ("domain/gap.py" in named) is edge


def test_an_attribute_off_a_call_handed_a_named_module_is_the_attribute():
    """``pkgutil.resolve_name("kodezart.domain:gap").compute_gap`` is compute_gap.

    The call's argument names the module, so the attribute is read off the
    module itself; the same call on a string naming nothing refers to nothing.
    """
    source = (
        "import pkgutil\n"
        "\n"
        "def plan(rows):\n"
        "    return pkgutil.resolve_name('kodezart.domain:gap').compute_gap(rows)\n"
    )
    missing = source.replace("kodezart.domain:gap", "kodezart.domain:absent")

    def found(text):
        return referencing_definitions(
            "services/planted.py",
            ast.parse(text),
            module_namespace("services/planted.py", text),
            wanted=(compute_gap,),
        )

    assert [name for name, _node in found(source)] == ["plan"]
    assert found(missing) == ()


def test_a_loaded_name_is_read_by_the_value_it_is_bound_to():
    """A name bound in the module, by an import or by a local is read as its value."""
    source = (
        "import kodezart.domain.fire_spec as spec\n"
        "\n"
        "LOCAL = 'bound here'\n"
        "\n"
        "def plan(rows):\n"
        "    from kodezart.domain.fire_spec import DELIVERABLES_SECTION\n"
        "\n"
        "    section = DELIVERABLES_SECTION\n"
        "    return (LOCAL, DELIVERABLES_SECTION, spec.DELIVERABLES_SECTION, rows,"
        " section)\n"
    )
    tree_of = ast.parse(source)
    namespace = module_namespace("services/planted.py", source)
    returned = next(node for node in ast.walk(tree_of) if isinstance(node, ast.Tuple))
    local, imported, attribute, parameter, assigned = returned.elts
    definition = next(
        node for node in ast.walk(tree_of) if isinstance(node, ast.FunctionDef)
    )

    def values(node):
        return set(loaded_values("services/planted.py", tree_of, namespace, node))

    assert values(local) == {"bound here"}
    assert values(imported) == {"Deliverables"}
    assert "Deliverables" in values(attribute)
    assert values(parameter) == set()
    assert values(definition) >= {"bound here", "Deliverables"}
    # The local is followed only with the definition it is assigned in: read
    # on its own, the name is bound by nothing the module states.
    assert values(assigned) == set()


def referrers(source, *, wanted=(compute_gap,), relative="services/planted.py"):
    """The definitions of a planted module text that refer to *wanted*."""
    return [
        name
        for name, _node in referencing_definitions(
            relative,
            ast.parse(source),
            module_namespace(relative, source),
            wanted=wanted,
        )
    ]


#: Each way a literal name reads an attribute off an object, as the text that
#: binds ``window`` inside the planted function; every one is ``compute_gap``
#: read off the gap module, or off the package the module hangs from.
LITERAL_NAME_ROUTES = {
    "getattr": "getattr(gap, 'compute_gap')",
    "getattr with a default": "getattr(gap, 'compute_gap', None)",
    "getattr off a dotted route": "getattr(kodezart.domain.gap, 'compute_gap')",
    "attrgetter applied": "operator.attrgetter('compute_gap')(gap)",
    "attrgetter over a dotted path": "operator.attrgetter('gap.compute_gap')(domain)",
    "attrgetter imported by name": "attrgetter('compute_gap')(gap)",
    "vars": "vars(gap)['compute_gap']",
    "__dict__": "gap.__dict__['compute_gap']",
}


@pytest.mark.parametrize("route", sorted(LITERAL_NAME_ROUTES))
def test_a_literal_name_is_the_attribute_of_what_its_receiver_denotes(route):
    """``getattr(x, "name")`` and its kin are read as that attribute of ``x``.

    The receiver is resolved by object and the literal is read off it
    statically; the same text with a literal naming nothing refers to
    nothing, and so does the same literal read off a value handed in.
    """
    header = (
        "import kodezart.domain.gap\n"
        "import operator\n"
        "from operator import attrgetter\n"
        "from kodezart import domain\n"
        "from kodezart.domain import gap\n"
        "\n"
    )
    source = (
        f"{header}def plan(rows):\n"
        f"    window = {LITERAL_NAME_ROUTES[route]}\n"
        "    return window(rows, supersession_refs={})\n"
    )
    handed = source.replace("(gap)", "(rows)").replace("(gap,", "(rows,")
    handed = handed.replace("(domain)", "(rows)").replace(
        "gap.__dict__", "rows.__dict__"
    )
    handed = handed.replace("kodezart.domain.gap, ", "rows, ")
    assert "def plan" in handed and handed != source

    assert referrers(source) == ["plan"]
    assert referrers(source.replace("compute_gap", "absent")) == []
    assert referrers(handed) == []


#: Each expression a local can be assigned from inside a definition and then
#: read ``compute_gap`` off, as the text that binds ``arithmetic``.
LOCAL_BINDINGS = {
    "resolve_name of the module": "pkgutil.resolve_name('kodezart.domain:gap')",
    "import_module of the module": "importlib.import_module('kodezart.domain.gap')",
    "the imported module": "gap",
    "vars of the package": "vars(domain)['gap']",
    "getattr off the package": "getattr(domain, 'gap')",
}


@pytest.mark.parametrize("binding", sorted(LOCAL_BINDINGS))
def test_a_local_assigned_inside_the_definition_is_followed(binding):
    """A local bound from an expression read here is read as what it denotes.

    Assigned plainly, annotated, by walrus, or through a second local; the
    attribute read off it is resolved off the object.  A local bound from a
    value handed in denotes nothing.
    """
    header = (
        "import importlib\n"
        "import pkgutil\n"
        "from kodezart import domain\n"
        "from kodezart.domain import gap\n"
        "\n"
    )
    value = LOCAL_BINDINGS[binding]
    forms = {
        "assigned": f"    arithmetic = {value}\n",
        "annotated": f"    arithmetic: object = {value}\n",
        "walrus": f"    if (arithmetic := {value}):\n        pass\n",
        "through a second local": f"    first = {value}\n    arithmetic = first\n",
    }
    for form in forms.values():
        source = (
            f"{header}def plan(rows):\n{form}"
            "    return arithmetic.compute_gap(rows, supersession_refs={})\n"
        )
        assert referrers(source) == ["plan"], form
    handed = (
        f"{header}def plan(rows, held):\n"
        "    arithmetic = held\n"
        "    return arithmetic.compute_gap(rows, supersession_refs={})\n"
    )
    assert referrers(handed) == []


def test_a_closure_reads_the_locals_of_the_function_enclosing_it():
    source = (
        "import pkgutil\n"
        "\n"
        "def outer(rows):\n"
        "    arithmetic = pkgutil.resolve_name('kodezart.domain:gap')\n"
        "\n"
        "    def inner():\n"
        "        return arithmetic.compute_gap(rows, supersession_refs={})\n"
        "\n"
        "    return inner\n"
    )
    assert referrers(source) == ["outer", "outer.inner"]


def test_a_local_assigned_from_itself_ends_the_walk():
    source = (
        "from kodezart.domain import gap\n"
        "\n"
        "def plan(rows):\n"
        "    arithmetic = arithmetic.compute_gap\n"
        "    window = gap\n"
        "    gap = window\n"
        "    return arithmetic(rows), window\n"
    )
    assert referrers(source, wanted=(gap_module,)) == ["plan"]
    assert referrers(source) == []


#: Each shape outside the reach, as a planted module text: a value handed
#: across a function boundary, a name built at run time, and a binding made
#: only when a function runs.  None refers to the arithmetic in ``plan``.
UNSEEN_SHAPES = {
    "an argument": "def plan(rows, window):\n"
    "    return window(rows, supersession_refs={})\n",
    "returned from a helper": "from kodezart.domain import gap\n"
    "\n"
    "def arithmetic():\n"
    "    return gap.compute_gap\n"
    "\n"
    "def plan(rows):\n"
    "    return arithmetic()(rows, supersession_refs={})\n",
    "stored on an object and read elsewhere": "from kodezart.domain import gap\n"
    "\n"
    "class Holder:\n"
    "    def __init__(self):\n"
    "        self.window = gap.compute_gap\n"
    "\n"
    "def plan(rows, holder):\n"
    "    return holder.window(rows, supersession_refs={})\n",
    "passed through a container built elsewhere": "from kodezart.domain import gap\n"
    "\n"
    "def table():\n"
    "    return {'window': gap.compute_gap}\n"
    "\n"
    "def plan(rows):\n"
    "    return table()['window'](rows, supersession_refs={})\n",
    "a name built at run time": "from kodezart.domain import gap\n"
    "\n"
    "def plan(rows):\n"
    "    window = getattr(gap, 'compute_' + 'gap')\n"
    "    return window(rows, supersession_refs={})\n",
    "globals() bound inside a function": "from kodezart.domain import gap\n"
    "\n"
    "def bind():\n"
    "    globals()['window'] = gap.compute_gap\n"
    "\n"
    "def plan(rows):\n"
    "    return window(rows, supersession_refs={})\n",
    "setattr inside a function": "import sys\n"
    "\n"
    "from kodezart.domain import gap\n"
    "\n"
    "def bind():\n"
    "    setattr(sys.modules[__name__], 'window', gap.compute_gap)\n"
    "\n"
    "def plan(rows):\n"
    "    return window(rows, supersession_refs={})\n",
}


@pytest.mark.parametrize("shape", sorted(UNSEEN_SHAPES))
def test_a_shape_outside_the_reach_is_not_a_reference(shape):
    """The stated limit, held: ``plan`` refers to nothing in each shape.

    Where the module binds the arithmetic elsewhere — in the helper, the
    holder or the binder — that definition is the reference, and ``plan``,
    which reaches the value only across the boundary, is not.
    """
    assert "plan" not in referrers(UNSEEN_SHAPES[shape])
