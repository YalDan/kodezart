"""A real tree, real checks, and the doubles that read them.

Nothing here stipulates what a check is worth.  The tree is written to disk,
the checks are real source, and each one is really run against whichever copy
of the tree it is asked about — so "this check still passes with the
behaviour it names gone" is an observation of source rather than a claim a
double was scripted with.

Three checks over two behaviours:

* ``wired`` constructs a double, hands it to the subject and asserts on what
  the subject returned, so it fails once the subject stops doing the work;
* ``tautology`` asserts about its own inputs and never calls the subject;
* ``unwired`` constructs a double for the subject's collaborator, hands it to
  nothing and asserts about the double.

The last two therefore pass in every copy of the tree, whatever was removed
from it, and the first one does not.
"""

import hashlib
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from tests.fakes import FakeWorkspaceProvider

#: The production module the checks are about: two behaviours, each reached
#: through a collaborator a check can stand a double in for.
BEHAVIOUR = '''"""Two behaviours a criterion can name."""


def discount(total, rate, *, rounder):
    """Take *rate* off *total*, rounded by the collaborator."""
    return rounder.round(total * (1 - rate))


def label(kind, *, naming):
    """Name *kind* through the collaborator."""
    return naming.name_for(kind)
'''

#: A check that observes the subject: the double is handed to ``discount``
#: and the assertions are about what ``discount`` did with it.
WIRED = '''"""Hands its double to the subject and asserts on the answer."""

from behaviour import discount


class Rounder:
    def __init__(self):
        self.asked = []

    def round(self, value):
        self.asked.append(value)
        return round(value, 2)


def check():
    rounder = Rounder()
    assert discount(200.0, 0.25, rounder=rounder) == 150.0
    assert rounder.asked == [150.0]
'''

#: A check that observes its own inputs and nothing else.
TAUTOLOGY = '''"""Asserts about the values it built itself; calls no subject."""


def check():
    kind = "urgent"
    assert kind == "urgent"
    assert len(kind) == len("urgent")
'''

#: A check whose double is constructed and handed to none of the subjects
#: under test, so every assertion in it is about the double.
UNWIRED = '''"""Builds a double for the subject's collaborator and wires it nowhere."""


class Naming:
    def name_for(self, kind):
        return f"<{kind}>"


def check():
    naming = Naming()
    assert naming.name_for("urgent") == "<urgent>"
    assert naming.name_for("routine") == "<routine>"
'''

CHECKS = {"wired": WIRED, "tautology": TAUTOLOGY, "unwired": UNWIRED}

#: The production module a removal is expected to reach.
BEHAVIOUR_PATH = Path("src") / "behaviour.py"

#: The same module with both behaviours emptied: the tree a removal leaves.
REMOVED = '''"""Two behaviours a criterion can name."""


def discount(total, rate, *, rounder):
    """Take *rate* off *total*, rounded by the collaborator."""
    return None


def label(kind, *, naming):
    """Name *kind* through the collaborator."""
    return None
'''


def write_fixture_tree(path: Path) -> None:
    """Write the whole fixture tree under *path*, checks and behaviour alike."""
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "checks").mkdir(parents=True, exist_ok=True)
    (path / BEHAVIOUR_PATH).write_text(BEHAVIOUR)
    for name, source in CHECKS.items():
        (path / "checks" / f"{name}.py").write_text(source)


def remove_the_behaviour(path: Path) -> None:
    """Empty both behaviours in the tree at *path*, leaving it importable.

    What a removing session's product is: a tree, not a claim about one.
    """
    (path / BEHAVIOUR_PATH).write_text(REMOVED)


def tree_digest(path: Path) -> str:
    """One value over every byte of the fixture tree at *path*.

    The dirtiness of a copy is read by comparing this against the value the
    same tree had when it was handed out, so a tree nothing edited answers
    "no changes" because nothing edited it.
    """
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*.py")):
        digest.update(str(file.relative_to(path)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"the fixture tree holds no loadable {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_named_check(tree: Path, name: str) -> bool:
    """Really run the named check against the tree at *tree*.

    Loaded under a fresh module name per call, so two copies of one tree are
    two readings and not one cached answer.  An ``AssertionError`` is the
    check failing; anything else propagates, because a check that cannot run
    is a broken fixture rather than a failing check.
    """
    behaviour = _load(tree / BEHAVIOUR_PATH, f"behaviour_{uuid4().hex}")
    held = sys.modules.get("behaviour")
    sys.modules["behaviour"] = behaviour
    try:
        module = _load(tree / "checks" / f"{name}.py", f"check_{name}_{uuid4().hex}")
        try:
            module.check()
        except AssertionError:
            return False
        return True
    finally:
        if held is None:
            sys.modules.pop("behaviour", None)
        else:
            sys.modules["behaviour"] = held


def fixture_evaluation(tree: Path, checks: dict[str, str]) -> dict:
    """One evaluator echo per criterion, each carrying its check's real outcome.

    *checks* maps a criterion key to the check that criterion names.  The
    answer is the outcome of running that check in *tree*, so what the
    grading says is what the source did.
    """
    return {
        "criteriaResults": [
            {
                "criterionId": key,
                "criterion": "an evaluator echo",
                "passed": run_named_check(tree, name),
                "reasoning": f"Ran the check {name} names.",
            }
            for key, name in checks.items()
        ]
    }


#: A line of the removal template stable enough to recognise the role by.
#:
#: Recognised by its own words rather than by "a session with no output
#: format", which is what an implementation session also looks like: a double
#: that could not tell them apart would count one as the other.
MUTATION_REMOVAL_LINE = "Remove from this workspace the behaviour"


class FixtureWorkspaces(FakeWorkspaceProvider):
    """A provider handing out a real copy of the fixture tree per acquisition.

    A path per acquisition, never one path for every acquire: the whole
    measurement is that a second tree differs from the first, and a provider
    answering with one path could not hold two trees at once.  Everything
    else about it — capture, resume, the recorded calls — is the shared
    double's, so a lane driven over these trees is driven over the same
    workspace behaviour every other lane is.
    """

    def __init__(self, *, root: Path, git=None) -> None:
        super().__init__(git=git)
        self._root = root
        #: ``(path, ref)`` per acquisition, in order.
        self.acquired: list[tuple[str, str]] = []
        self.released: list[str] = []
        self._handed: dict[str, str] = {}
        #: Run against each path as it is handed out, for a case whose subject
        #: is a copy that stopped being the tree the ref names before its
        #: holder read it back.  A hook rather than a replaced ``acquire``, so
        #: every case still acquires through the one method the lane calls.
        self.on_acquired: Callable[[str], None] | None = None

    async def acquire(self, *, ref: str, **rest) -> str:
        path = self._root / f"tree-{len(self.acquired)}"
        write_fixture_tree(path)
        self._workspace_path = str(path)
        await super().acquire(ref=ref, **rest)
        self._handed[str(path)] = tree_digest(path)
        self.acquired.append((str(path), ref))
        if self.on_acquired is not None:
            self.on_acquired(str(path))
        return str(path)

    async def release(self, workspace_path: str) -> None:
        self.released.append(workspace_path)
        await super().release(workspace_path)

    def read_through(self, git) -> None:
        """Read worktree identity through the git double the lane reads through.

        One repository, so the tree a lane captures is a tree whose head the
        lane's own reads answer for: two doubles here would hand back a
        snapshot of a commit nothing in the lane stands at.
        """
        self._git = git

    def differs(self, path: str) -> bool:
        """Whether the tree at *path* is no longer the tree handed out there."""
        held = self._handed.get(path)
        if held is None:
            return False
        return tree_digest(Path(path)) != held


class MutationLaneFixture:
    """One value: the trees a lane is graded in and the checks it is graded on.

    Both together, because a fixture supplying a tree without the checks that
    name it would grade one thing and measure another.
    """

    def __init__(self, *, root: Path, checks: dict[str, str]) -> None:
        self.checks = dict(checks)
        self.workspaces = FixtureWorkspaces(root=root)

    def evaluate_in(self, workspace: str | None) -> dict:
        """Grade every criterion by really running the check it names."""
        return fixture_evaluation(Path(workspace or ""), self.checks)

    def remove_in(self, workspace: str | None) -> None:
        """What a removing session leaves behind: an emptied tree."""
        remove_the_behaviour(Path(workspace or ""))
