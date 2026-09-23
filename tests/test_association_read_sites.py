"""Re-entry reads branch associations off the record, never off attachments.

The record on the lane's issue is the source for which branches a run is
about (KOD-96).  A second carrier for the same fact — a file attached to the
issue, listed and read back beside the record — is how the two answers come
apart: one of them is refreshed on a write the other does not see, and
nothing red says so.  So the modules that read associations and the modules
that touch the issue's attachment surface are disjoint sets, which is what
this guard keeps true.

Nothing is listed by hand that the tree can be asked for.  The association
names are the record's own association type, its field on the record, and the
name of every function in the package whose body reads that field — the
queries over it, found by the walk, so a module that asks one of them for the
branches is a reader as surely as one that reads the field itself.  The
attachment names are the vendor payload's own field, the port member that
lists assets and the asset type it answers with.  The scanned tree is the
shipped package itself, so a new module is inside the walk the moment it is
written.

The walk is textual and executes nothing, which is what lets it speak for
the whole tree.  Its blind spots, which review has to read from the code
instead: a name composed at run time and reached by ``getattr``, and an
attachment read made through a helper that names neither the port member nor
the asset type.
"""

import ast
import sys
from pathlib import Path

import pytest

from kodezart.adapters.linear.wire import LinearAssetWire, LinearIssueDetailWire
from kodezart.core.protocols import TrackerPort
from kodezart.domain.lane_entry import recorded_branches
from kodezart.domain.lane_record import associated_branches
from kodezart.services import lane_entry
from kodezart.types.domain.branch import BranchAssociation
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerAsset

#: The shipped package, and so the tree this guard speaks for.
SOURCE = Path(sys.modules["kodezart"].__file__ or "").resolve().parent

#: The record's own field holding its branch associations, read off the model
#: rather than spelled here, so renaming it moves the walk with it.
ASSOCIATION_FIELDS = frozenset(
    name
    for name, field in LaneRunState.model_fields.items()
    if BranchAssociation.__name__ in str(field.annotation)
)

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def reads_the_field(node: ast.AST) -> bool:
    """Whether *node*'s body reads the association field off a record.

    An attribute read of the field, or the field's name as a mapping key.  A
    keyword argument naming it is a construction, not a read.
    """
    return any(
        (isinstance(item, ast.Attribute) and item.attr in ASSOCIATION_FIELDS)
        or (isinstance(item, ast.Constant) and item.value in ASSOCIATION_FIELDS)
        for item in ast.walk(node)
    )


def reading_functions(root: Path) -> frozenset[str]:
    """The name of every function under *root* whose body reads the field."""
    return frozenset(
        node.name
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, FUNCTIONS) and reads_the_field(node)
    )


#: The functions in the package that read the associations off a record: the
#: queries a module asks for the branches instead of reading the field.
READING_FUNCTIONS = reading_functions(SOURCE)
#: Every name a module that reads the recorded associations must spell.
ASSOCIATION_NAMES = (
    frozenset({BranchAssociation.__name__}) | ASSOCIATION_FIELDS | READING_FUNCTIONS
)

#: The vendor payload's attachment array, read off the wire model by the asset
#: entry it carries.
ATTACHMENT_FIELDS = frozenset(
    name
    for name, field in LinearIssueDetailWire.model_fields.items()
    if LinearAssetWire.__name__ in str(field.annotation)
)
#: Every name a module that touches the issue's attachment surface spells.
ATTACHMENT_NAMES = (
    frozenset({TrackerPort.list_issue_assets.__name__, TrackerAsset.__name__})
    | ATTACHMENT_FIELDS
)

#: The adapter that speaks the vendor's attachment payload: the one module the
#: attachment surface must contain, so a walk that has stopped seeing the
#: surface at all cannot pass by seeing nothing.
ADAPTER = "adapters/linear/tracker.py"

#: The service that decides a lane's entry from its record: a reader only
#: through the query it asks.
LANE_ENTRY_SERVICE = (
    Path(lane_entry.__file__ or "").resolve().relative_to(SOURCE).as_posix()
)


def named(tree: ast.AST) -> frozenset[str]:
    """Every name this module spells, in any of the forms a read takes.

    An attribute or a bare name, an imported or aliased binding, a parameter
    or keyword, a definition's own name, and a string constant that IS one of
    the names — a mapping key carries the field as surely as an attribute
    does.  Prose is not matched: a docstring mentioning the word is a whole
    string of its own and equals no name.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name.rpartition(".")[2])
        elif isinstance(node, DEFINITIONS):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return frozenset(found)


def surfaces(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """The association readers and the attachment sites under *root*.

    A reader spells one of the package's association names or calls a
    function *root* itself defines that reads the field.
    """
    readers: set[str] = set()
    attachments: set[str] = set()
    association_names = ASSOCIATION_NAMES | reading_functions(root)
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        spelled = named(ast.parse(path.read_text()))
        if spelled & association_names:
            readers.add(module)
        if spelled & ATTACHMENT_NAMES:
            attachments.add(module)
    return frozenset(readers), frozenset(attachments)


def test_the_names_the_walk_keys_on_are_productions_own():
    """Each half is derived from one model or port member, not from a list."""
    assert len(ASSOCIATION_FIELDS) == 1
    assert len(ATTACHMENT_FIELDS) == 1
    assert ASSOCIATION_NAMES.isdisjoint(ATTACHMENT_NAMES)
    assert (SOURCE / ADAPTER).is_file()
    # The walk finds the queries the entry and record services ask, rather
    # than this module naming them: both read the field in their own bodies.
    assert {associated_branches.__name__, recorded_branches.__name__} <= (
        READING_FUNCTIONS
    )


def test_no_module_that_reads_associations_names_an_attachment_read():
    """The two sets are disjoint, and each of them has something in it.

    A module reading the recorded associations reads them off the record it
    was handed; nothing on that path lists or fetches what is attached to the
    issue, so no branch fact is ever taken from a second carrier.
    """
    readers, attachments = surfaces(SOURCE)
    assert readers and attachments
    assert ADAPTER in attachments
    # A module that reaches the associations only through a query is a
    # reader: the lane entry service asks for the recorded branches.
    assert LANE_ENTRY_SERVICE in readers
    assert readers.isdisjoint(attachments), sorted(readers & attachments)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "async def reenter(port, record, key):\n"
            "    assets = await port.list_issue_assets(issue_key=key)\n"
            "    return {item.branch for item in record.associations} | set(assets)\n",
            id="one-function-reads-both",
        ),
        pytest.param(
            "from kodezart.domain.lane_record import associated_branches\n"
            "async def attached(port, key):\n"
            "    return await port.list_issue_assets(issue_key=key)\n"
            "def branches(record):\n"
            "    return associated_branches(record=record)\n",
            id="two-functions-one-module",
        ),
        pytest.param(
            "def branches(record):\n"
            "    asset: TrackerAsset = record.associations[0]\n"
            "    return asset\n",
            id="annotation-only",
        ),
        pytest.param(
            "def branches(payload):\n"
            "    return payload['associations'], payload['attachments']\n",
            id="mapping-keys",
        ),
        pytest.param(
            "from kodezart.domain.lane_entry import recorded_branches\n"
            "async def reenter(port, record, key):\n"
            "    assets = await port.list_issue_assets(issue_key=key)\n"
            "    return recorded_branches(record=record), assets\n",
            id="query-beside-assets",
        ),
    ],
)
def test_a_module_reading_associations_beside_an_attachment_is_reported(body, tmp_path):
    readers, attachments = surfaces(plant(tmp_path, body))
    assert PLANTED in readers & attachments


def plant(root: Path, body: str) -> Path:
    """A package under *root* holding one planted module and nothing else."""
    module = root / PLANTED
    module.parent.mkdir(parents=True)
    module.write_text(body)
    return root


#: Where a planted control sits in its package: a services-shaped module.
PLANTED = "services/planted.py"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def branches(record):\n"
            "    return {item.branch for item in record.associations}\n",
            id="associations-alone",
        ),
        pytest.param(
            '"""Re-entry never reads the attachments a lane issue carries."""\n'
            "def branches(record):\n"
            "    return record.associations\n",
            id="prose-about-attachments",
        ),
    ],
)
def test_a_module_that_only_reads_associations_is_not_reported(body, tmp_path):
    readers, attachments = surfaces(plant(tmp_path, body))
    assert PLANTED in readers
    assert PLANTED not in attachments
