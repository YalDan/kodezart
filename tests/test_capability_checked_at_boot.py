"""Capability and totality are asked once, at boot, and never branched on.

The scanned surface is derived: every module under the packaged source tree,
walked from the package's own path, and every field of the two configuration
models, walked recursively. What each scan matches, and nothing more:

- a call of the port's probe method, by its name;
- a call of either boot check, by its name;
- an ``except`` clause naming either refusal type, alone or in a tuple (a
  broad handler such as ``except Exception`` is not scanned);
- a membership test against the fold table, a ``.get`` on it, and a read of a
  row's ``scans``, which only the two boot checks may make (a subscript read
  of a row is allowed, because a stored record is replayed through it);
- a configuration field whose annotation names the signal vocabulary's type;
- a configuration field whose name contains ``alarm`` and whose annotation is
  anything but a number;
- in every function that reads the set of alarms the supervisor observes
  (the supervisor arm of ``_verify_wired_gates`` and the lane observation's
  ``_announceable`` among them), a configuration read inside the statements
  that read that set other than one of the ``run_alarm_*`` numeric bounds.

The last scan is keyed on the consumer rather than on a knob's name, and is
resolved by object: each module is imported and every name, attribute and
annotation is read for the object it denotes in the module's own namespace,
through ``tests/name_resolution.py``'s bindings. It follows an import alias, a
module route and an assignment alias of the observed set; a parameter
annotated with either configuration model, directly, in a union or as a
string, in the function or in an enclosing one; an assignment alias of that
parameter; a local bound anywhere in the function from a configuration read
and read inside those statements; and a string constant naming a
configuration field (``getattr(config, "field")`` and its kin).

Outside its reach, as for every static guard here: a value handed across a
function boundary, where the other function is not resolved at this site
(returned from a helper, stored on an object and read elsewhere, or passed
through a container built elsewhere); a name built at run time; a binding made
only when a function runs (``setattr`` or ``globals()`` inside a function
body).

Each has a synthetic control proving it finds what it looks for, and each
shape outside its reach a control holding it unseen.
"""

import ast
import importlib
import importlib.util
import inspect
import re
import types
import typing
from collections.abc import Iterator, Mapping
from pathlib import Path

from pydantic import BaseModel

import kodezart
from kodezart.config.app import AppConfig
from kodezart.core.errors import PassGateCapabilityError
from kodezart.domain.lane_alarms import OBSERVED_ALARMS
from kodezart.domain.run_alarm_table import AlarmTableError, require_alarm_table
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmSignal
from tests.name_resolution import bound_names, definitions, parameters_of, source_tree

PACKAGE_ROOT = Path(kodezart.__path__[0])
#: The one function allowed to ask the credential what it can scan.
PROBE = "verify_scan_capability"
PROBE_SITE = ("composition/passes.py", "_verify_wired_gates")
#: The probe's own helper, which reads the alarm table for the scans the
#: supervisor tick's alarms declare.
SCANS_SITE = ("composition/passes.py", "_supervisor_scans")
#: The boot checks, and the one function allowed to call each of them.
BOOT_CHECKS = ("_verify_wired_gates", require_alarm_table.__name__)
BOOT_SITE = ("composition/passes.py", "verify_pass_preflight")
#: The two functions allowed to ask whether the fold table has a row or what
#: a row scans: the totality check and the probe that inverts the column.
TABLE_QUESTION_SITES = frozenset(
    {
        ("domain/run_alarm_table.py", require_alarm_table.__name__),
        SCANS_SITE,
    }
)
TABLE = "ALARM_TABLE"
REFUSALS = frozenset({PassGateCapabilityError.__name__, AlarmTableError.__name__})


def packaged_sources() -> dict[str, str]:
    """Every module of the package, by path relative to the package root."""
    return {
        path.relative_to(PACKAGE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
    }


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Every function in *tree* with its innermost enclosing function's name."""

    def walk(node: ast.AST, owner: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            yield name, child
            yield from walk(child, name)

    yield from walk(tree, "<module>")


def call_sites(sources: Mapping[str, str], name: str) -> set[tuple[str, str]]:
    """Every ``(path, function)`` that calls *name*, as a name or an attribute."""
    sites: set[tuple[str, str]] = set()
    for path, text in sources.items():
        for owner, node in _functions(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if called == name:
                sites.add((path, owner))
    return sites


def _named(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        return {name for element in node.elts for name in _named(element)}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Name):
        return {node.id}
    return set()


def handlers_of(sources: Mapping[str, str], names: frozenset[str]) -> list[str]:
    """Every except clause naming one of *names*, alone or in a tuple."""
    return sorted(
        f"{path}:{node.lineno}"
        for path, text in sources.items()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ExceptHandler) and _named(node.type) & names
    )


def _names_table(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == TABLE) or (
        isinstance(node, ast.Attribute) and node.attr == TABLE
    )


def table_questions(sources: Mapping[str, str]) -> set[tuple[str, str]]:
    """Every ``(path, function)`` asking whether the fold table has a row.

    A membership test against the table, a ``.get`` on it, and a read of any
    row's ``scans``. A subscript read of a row is not a question: it assumes
    the row, which the boot has already guaranteed.
    """
    sites: set[tuple[str, str]] = set()
    for path, text in sources.items():
        for owner, node in _functions(ast.parse(text)):
            asked = (
                (
                    isinstance(node, ast.Compare)
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                    and any(
                        _names_table(side) for side in (node.left, *node.comparators)
                    )
                )
                or (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and _names_table(node.func.value)
                )
                or (isinstance(node, ast.Attribute) and node.attr == "scans")
            )
            if asked:
                sites.add((path, owner))
    return sites


def _numeric(annotation: object) -> bool:
    if typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    return annotation in (int, float)


def alarm_knobs(
    model: type[BaseModel], ancestors: frozenset[type] = frozenset()
) -> list[str]:
    """Every field, through nested models, named for alarms and not a number.

    A number is a bound; a string or a collection under an alarm's name is a
    way to spell which alarms to leave out. Each path to a nested model is
    walked, and only a model already on the current path is not re-entered.
    """
    path = ancestors | {model}
    found: list[str] = []
    for name, field in model.model_fields.items():
        if "alarm" in name and not _numeric(field.annotation):
            found.append(name)
        for nested in _models_in(field.annotation):
            if nested not in path:
                found.extend(f"{name}.{inner}" for inner in alarm_knobs(nested, path))
    return sorted(found)


def _models_in(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _models_in(arg)


def _mentions(annotation: object, target: type, seen: set[type]) -> bool:
    """Whether *annotation* names *target*, through containers and models."""
    if annotation is target:
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(
            _mentions(field.annotation, target, seen)
            for field in annotation.model_fields.values()
        )
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Annotated:
        args = args[:1]
    if isinstance(annotation, types.UnionType) or origin is not None:
        return any(_mentions(arg, target, seen) for arg in args)
    return False


def fields_naming(model: type[BaseModel], target: type) -> list[str]:
    """Every field of *model*, walked through nested models, naming *target*."""
    return sorted(
        name
        for name, field in model.model_fields.items()
        if _mentions(field.annotation, target, {model})
    )


#: The configuration models a knob could live on.
CONFIG_MODELS = (AppConfig, OperationConfig)
#: What a reader of the observed set may take off a configuration: the alarm
#: bounds, each a number, so a value set there moves a threshold and never
#: names which alarms are observed.
ALARM_BOUNDS = frozenset(
    name
    for name, field in AppConfig.model_fields.items()
    if name.startswith("run_alarm_") and _numeric(field.annotation)
)
#: The two readers of the observed set this scan is written for: the boot's
#: supervisor arm, which declares the observed alarms' scans (and takes no
#: configuration at all: whether the tick runs is its cadence pair, read by
#: its caller), and the lane observation, which announces the observed
#: alarms' transitions.
OBSERVED_CONSUMERS = frozenset(
    {
        ("composition/passes.py", "_supervisor_scans"),
        ("services/alarm_supervisor.py", "_announceable"),
    }
)

type Module = tuple[ast.Module, Mapping[str, object]]
type Function = ast.FunctionDef | ast.AsyncFunctionDef

_UNRESOLVED = object()


def _dotted(relative: str) -> str:
    parts = Path(relative).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((kodezart.__name__, *parts))


def packaged_modules() -> dict[str, Module]:
    """Every module of the package, parsed, beside its namespace after import."""
    return {
        relative: (
            ast.parse(source),
            vars(importlib.import_module(_dotted(relative))),
        )
        for relative, source in source_tree().items()
    }


def _denoted(node: ast.expr, namespace: Mapping[str, object]) -> object:
    """The object a name or an attribute chain denotes in *namespace*."""
    if isinstance(node, ast.Name):
        return namespace.get(node.id, _UNRESOLVED)
    if isinstance(node, ast.Attribute):
        owner = _denoted(node.value, namespace)
        if isinstance(owner, types.ModuleType | type):
            try:
                return inspect.getattr_static(owner, node.attr)
            except AttributeError:
                return _UNRESOLVED
    return _UNRESOLVED


def _names_config(annotation: ast.expr, namespace: Mapping[str, object]) -> bool:
    """Whether an annotation denotes either configuration model anywhere in it."""
    for node in ast.walk(annotation):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                written = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                continue
            if _names_config(written, namespace):
                return True
        elif isinstance(node, ast.Name | ast.Attribute):
            denoted = _denoted(node, namespace)
            if any(denoted is model for model in CONFIG_MODELS):
                return True
    return False


def _config_fields(model: type[BaseModel], seen: set[type]) -> set[str]:
    """Every field name of *model*, walked through nested models."""
    seen.add(model)
    found = set(model.model_fields)
    for field in model.model_fields.values():
        for nested in _models_in(field.annotation):
            if nested not in seen:
                found |= _config_fields(nested, seen)
    return found


def _within(function: Function) -> ast.Module:
    return ast.Module(body=[function], type_ignores=[])


def _knob_reads(
    node: ast.AST,
    *,
    receivers: frozenset[str],
    derived: frozenset[str],
    fields: frozenset[str],
) -> list[str]:
    """Every configuration read inside *node* other than an alarm bound."""
    found: list[str] = []
    for inner in ast.walk(node):
        if (
            isinstance(inner, ast.Attribute)
            and isinstance(inner.value, ast.Name)
            and inner.value.id in receivers
            and inner.attr not in ALARM_BOUNDS
        ):
            found.append(inner.attr)
        elif isinstance(inner, ast.Name) and inner.id in derived:
            found.append(inner.id)
        elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            found.extend(
                word
                for word in re.split(r"[^A-Za-z0-9_]+", inner.value)
                if word in fields
            )
    return found


def observed_consumers(
    modules: Mapping[str, Module], observed: object = OBSERVED_ALARMS
) -> dict[tuple[str, str], list[str]]:
    """Every function reading *observed*, with the knob reads beside that read.

    ``(module, definition)`` -> the configuration reads, other than an alarm
    bound, inside the function's own statements that read *observed*. A
    statement of the function's body is one of those when anything inside it
    denotes the observed set, or a local the module binds to it. The
    configuration is every parameter annotated with either model, in the
    function or in one enclosing it, and every local bound to one of those.
    """
    fields = frozenset(
        set().union(*(_config_fields(model, set()) for model in CONFIG_MODELS))
        - ALARM_BOUNDS
    )
    consumers: dict[tuple[str, str], list[str]] = {}
    for module, (tree, namespace) in sorted(modules.items()):
        consumers.update(
            _module_consumers(
                module, tree, namespace=namespace, observed=observed, fields=fields
            )
        )
    return consumers


def _module_consumers(
    module: str,
    tree: ast.Module,
    *,
    namespace: Mapping[str, object],
    observed: object,
    fields: frozenset[str],
) -> dict[tuple[str, str], list[str]]:
    aliases = bound_names(
        tree,
        yields=lambda value, names: (
            _denoted(value, namespace) is observed
            or (isinstance(value, ast.Name) and value.id in names)
        ),
    )

    def reads_observed(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.Name | ast.Attribute)
            and isinstance(inner.ctx, ast.Load)
            and (
                _denoted(inner, namespace) is observed
                or (isinstance(inner, ast.Name) and inner.id in aliases)
            )
            for inner in ast.walk(node)
        )

    where = definitions(tree)
    consumers: dict[tuple[str, str], list[str]] = {}

    def visit(node: ast.AST, inherited: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, inherited)
                continue
            typed = inherited | {
                argument.arg
                for argument in parameters_of(child)
                if argument.annotation is not None
                and _names_config(argument.annotation, namespace)
            }
            receivers = bound_names(
                _within(child),
                yields=lambda value, names: (
                    isinstance(value, ast.Name) and value.id in names
                ),
                seeds=typed,
            )
            derived = bound_names(
                _within(child),
                yields=lambda value, names, receivers=receivers: bool(
                    _knob_reads(
                        value, receivers=receivers, derived=names, fields=fields
                    )
                ),
            )
            region = [
                statement for statement in child.body if reads_observed(statement)
            ]
            if region:
                consumers[(module, where[id(child)])] = [
                    read
                    for statement in region
                    for read in _knob_reads(
                        statement, receivers=receivers, derived=derived, fields=fields
                    )
                ]
            visit(child, receivers)

    visit(tree, frozenset())
    return consumers


# ---------------------------------------------------------------------------
# The tree.
# ---------------------------------------------------------------------------


def test_the_credential_is_asked_what_it_can_scan_in_one_place():
    assert call_sites(packaged_sources(), PROBE) == {PROBE_SITE}


def test_the_boot_checks_are_made_by_the_preflight_alone():
    sources = packaged_sources()
    for check in BOOT_CHECKS:
        assert call_sites(sources, check) == {BOOT_SITE}, check


def test_no_code_catches_a_boot_refusal_and_carries_on():
    """A caught refusal is a degraded supervisor: the tick would run blind.

    Matched by the two refusal types' names, alone or in a tuple. A broad
    handler is not scanned here.
    """
    assert handlers_of(packaged_sources(), REFUSALS) == []


def test_no_configuration_field_can_name_an_alarm_signal():
    """No knob can switch a signal off, because no knob can name one."""
    assert fields_naming(AppConfig, AlarmSignal) == []
    assert fields_naming(OperationConfig, AlarmSignal) == []


def test_no_alarm_knob_of_any_spelling_is_anything_but_a_bound():
    """A field named for alarms is a number or nothing.

    Matched by the field's name; a knob under any other name is caught where
    the observed set is read, by the consumer scan below.
    """
    assert alarm_knobs(AppConfig) == []
    assert alarm_knobs(OperationConfig) == []


def test_only_the_boot_checks_ask_whether_the_fold_table_has_a_row():
    """No runtime branch on a fold's presence or on what a row scans."""
    assert table_questions(packaged_sources()) == TABLE_QUESTION_SITES


def test_the_readers_of_the_observed_alarms_take_no_knob_but_a_bound():
    """No knob, whatever it is named, can narrow what the supervisor observes.

    Keyed on the consumer: a knob that turned the supervisor arm off, or
    filtered the observed set, has to be read where the set is read, whatever
    the knob is called and whatever its type. The readers are derived; the
    two this scan is written for are required among them, and the bounds a
    reader may take are required to exist, so neither side is vacuous.
    """
    consumers = observed_consumers(packaged_modules())

    assert ALARM_BOUNDS
    assert set(consumers) >= OBSERVED_CONSUMERS, sorted(consumers)
    assert {site: reads for site, reads in consumers.items() if reads} == {}


# ---------------------------------------------------------------------------
# The controls: each scan finds what it looks for.
# ---------------------------------------------------------------------------

CONTROL = {
    "composition/passes.py": (
        "async def _verify_wired_gates(tracker):\n"
        "    await tracker.verify_scan_capability(signals=[])\n"
        "async def verify_pass_preflight():\n"
        "    require_alarm_table()\n"
    ),
    "services/tick.py": (
        "async def observe(tracker):\n"
        "    try:\n"
        "        await tracker.verify_scan_capability(signals=[])\n"
        "    except (ValueError, errors.PassGateCapabilityError):\n"
        "        return\n"
        "    try:\n"
        "        require_alarm_table()\n"
        "    except AlarmTableError:\n"
        "        pass\n"
    ),
}


def test_a_probe_outside_the_boot_check_is_found():
    assert call_sites(CONTROL, PROBE) == {PROBE_SITE, ("services/tick.py", "observe")}
    assert call_sites(CONTROL, require_alarm_table.__name__) == {
        BOOT_SITE,
        ("services/tick.py", "observe"),
    }


def test_a_caught_refusal_is_found_alone_and_in_a_tuple():
    assert handlers_of(CONTROL, REFUSALS) == [
        "services/tick.py:4",
        "services/tick.py:8",
    ]


def test_a_question_to_the_fold_table_is_found_in_every_spelling():
    sources = {
        **CONTROL,
        "domain/run_alarm_table.py": (
            "def require_alarm_table():\n"
            "    return [s for s in AlarmSignal if s not in ALARM_TABLE]\n"
            "def alarm_raised(record):\n"
            "    if record.signal not in ALARM_TABLE:\n"
            "        return False\n"
            "    return ALARM_TABLE[record.signal].fold(record)\n"
        ),
        "services/tick.py": (
            "def observe(signal):\n"
            "    return run_alarm_table.ALARM_TABLE.get(signal)\n"
            "def scanned(signal):\n"
            "    return tables.ALARM_TABLE[signal].scans\n"
            "def replayed(signal, record):\n"
            "    return ALARM_TABLE[signal].fold(record)\n"
        ),
    }

    assert table_questions(sources) == {
        ("domain/run_alarm_table.py", "require_alarm_table"),
        ("domain/run_alarm_table.py", "alarm_raised"),
        ("services/tick.py", "observe"),
        ("services/tick.py", "scanned"),
    }


def test_an_alarm_knob_that_is_not_a_number_is_found_through_nesting():
    class Inner(BaseModel):
        disabled_alarm_signals: frozenset[str] = frozenset()
        run_alarm_bound: int = 1

    class Knobs(BaseModel):
        run_alarm_max_ticks: int = 1
        alarm_mode: str = ""
        nested: Inner = Inner()
        listed: list[Inner] = []

    assert alarm_knobs(Knobs) == [
        "alarm_mode",
        "listed.disabled_alarm_signals",
        "nested.disabled_alarm_signals",
    ]


def test_a_field_naming_a_signal_is_found_through_nesting():
    class Inner(BaseModel):
        disabled: tuple[AlarmSignal, ...] = ()

    class Knobs(BaseModel):
        plain: int = 0
        direct: AlarmSignal | None = None
        nested: Inner = Inner()
        mapped: dict[str, list[AlarmSignal]] = {}

    assert fields_naming(Knobs, AlarmSignal) == ["direct", "mapped", "nested"]


def _control_modules(tmp_path: Path, sources: Mapping[str, str]) -> dict[str, Module]:
    """Synthetic modules, written out and imported, keyed like the package's."""
    modules: dict[str, Module] = {}
    for relative, source in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            f"control_{Path(relative).stem}", path
        )
        assert spec is not None
        assert spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        modules[relative] = (ast.parse(source), vars(loaded))
    return modules


_CONTROL_HEAD = (
    "from kodezart.config.app import AppConfig\n"
    "from kodezart.config.app import AppConfig as Settings\n"
    "from kodezart.domain import lane_alarms\n"
    "from kodezart.domain.lane_alarms import OBSERVED_ALARMS\n"
    "from kodezart.domain.lane_alarms import OBSERVED_ALARMS as seen\n"
    "from kodezart.types.domain.operation import OperationConfig\n"
    "SAME = OBSERVED_ALARMS\n"
)


def test_a_knob_read_beside_the_observed_set_is_found_in_every_followed_form(
    tmp_path,
):
    """Each form the consumer scan follows, and a bound it lets through."""
    modules = _control_modules(
        tmp_path,
        {
            "arms.py": _CONTROL_HEAD
            + (
                "def gated(config: AppConfig, operation: OperationConfig | None):\n"
                "    if operation is not None and not config.scans_optional:\n"
                "        return sorted(OBSERVED_ALARMS)\n"
                "    return []\n"
                "def routed(config: 'Settings'):\n"
                "    return [a for a in lane_alarms.OBSERVED_ALARMS\n"
                "            if a.value not in config.blind_signals]\n"
                "def aliased(config: Settings):\n"
                "    settings = config\n"
                "    observed = seen\n"
                "    return [a for a in observed if a.value in settings.muted]\n"
                "def early(config: AppConfig):\n"
                "    muted = config.muted_signals\n"
                "    return [a for a in SAME if a.value not in muted]\n"
                "def literal(config: AppConfig):\n"
                "    return [a for a in OBSERVED_ALARMS\n"
                "            if a in getattr(config, 'dispatch_pass_gate_signals')]\n"
                "def outer(operation: OperationConfig):\n"
                "    def inner():\n"
                "        return [a for a in OBSERVED_ALARMS if operation.muted]\n"
                "    return inner\n"
                "def bounded(config: AppConfig):\n"
                "    limit = config.run_alarm_max_commits_without_closure\n"
                "    return [(a, limit) for a in OBSERVED_ALARMS]\n"
                "def unobserved(config: AppConfig):\n"
                "    return config.muted_signals\n"
            ),
        },
    )

    assert observed_consumers(modules) == {
        ("arms.py", "gated"): ["scans_optional"],
        ("arms.py", "routed"): ["blind_signals"],
        ("arms.py", "aliased"): ["muted"],
        ("arms.py", "early"): ["muted"],
        ("arms.py", "literal"): ["dispatch_pass_gate_signals"],
        ("arms.py", "outer"): ["muted"],
        ("arms.py", "outer.inner"): ["muted"],
        ("arms.py", "bounded"): [],
    }


def test_a_knob_handed_across_a_function_boundary_is_outside_the_reach(tmp_path):
    """The stated limit, held: each shape stays unseen.

    A knob returned from a helper, one stored on an object and read in
    another method, one handed in a container built elsewhere, a name built
    at run time and a binding made only when a function runs.
    """
    modules = _control_modules(
        tmp_path,
        {
            "limits.py": _CONTROL_HEAD
            + (
                "def muted_of(config: AppConfig):\n"
                "    return config.muted_signals\n"
                "def returned(config: AppConfig):\n"
                "    return [a for a in OBSERVED_ALARMS if a in muted_of(config)]\n"
                "class Stored:\n"
                "    def __init__(self, config: AppConfig):\n"
                "        self._muted = config.muted_signals\n"
                "    def observe(self):\n"
                "        return [a for a in OBSERVED_ALARMS if a in self._muted]\n"
                "def contained(knobs: dict):\n"
                "    return [a for a in OBSERVED_ALARMS if a in knobs['muted']]\n"
                "def built(config: AppConfig, word: str):\n"
                "    return [a for a in OBSERVED_ALARMS\n"
                "            if a in getattr(config, word + '_signals')]\n"
                "def bound_late(config: AppConfig):\n"
                "    globals()['late'] = config\n"
                "    return [a for a in OBSERVED_ALARMS if a in late.muted]\n"
            ),
        },
    )

    assert observed_consumers(modules) == {
        ("limits.py", "returned"): [],
        ("limits.py", "Stored.observe"): [],
        ("limits.py", "contained"): [],
        ("limits.py", "built"): [],
        ("limits.py", "bound_late"): [],
    }
