"""Base resolution — one rule, exercised over every shape a stack can take.

Every fixture runs against the in-process fake tracker and the fake git
service.  No live workspace, no live remote, and — asserted rather than
intended — no read of any pull request's merge or open/closed state.

The module's centre of gravity is the *degenerate-not-special* test: a lane
with one blocker and a lane whose three blockers form a chain must traverse
identical code, because the singleton arm is reached by the frontier coming
back holding one element, not by anything asking how many blockers there
were.  An implementation carrying a single-blocker shortcut fails that test
and nothing else.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest

from kodezart.domain.base_resolution import resolve_base
from kodezart.domain.errors import (
    BaseIntegrationConflictError,
    BaseResolutionError,
    DuplicateWorkRefError,
)
from kodezart.services.agent_service import AgentService
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.branch import BaseInput, WorkRef, WorkRefRole
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeGitService,
    FakePRCreator,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVER_MODULE = REPO_ROOT / "src" / "kodezart" / "domain" / "base_resolution.py"

REPO_PATH = "/fixture/repo"
INTEGRATION_WORKSPACE = "/fixture/integration"
REMOTE = "fixture-remote"

#: Deliberately NOT "main".  Every arm that is supposed to reach the scope's
#: configured trunk must reach THIS string, so a resolver that fell back to
#: either shipped `"main"` default would be visible rather than plausible.
CONFIGURED_TRUNK = "fixture-trunk"

LANE = "LANE-1"


def ref(
    issue_id: str,
    branch: str,
    *,
    role: WorkRefRole = WorkRefRole.DELIVERABLE,
    sha: str | None = None,
) -> WorkRef:
    """One recorded work ref. ``sha`` defaults to a deterministic value."""
    return WorkRef(
        issue_id=issue_id,
        role=role,
        branch=branch,
        pushed_head_sha=f"sha-{branch}" if sha is None else sha,
        recorded_at=FIXTURE_EPOCH,
    )


def resolver(tracker: FakeTrackerPort, git: FakeGitService) -> BaseResolver:
    return BaseResolver(tracker=tracker, git=git, remote=REMOTE)


async def resolve(tracker: FakeTrackerPort, git: FakeGitService) -> object:
    return await resolver(tracker, git).resolve(
        issue_key=LANE,
        repo_path=REPO_PATH,
        integration_workspace=INTEGRATION_WORKSPACE,
        trunk=CONFIGURED_TRUNK,
        now=FIXTURE_EPOCH,
    )


def merge_calls(git: FakeGitService) -> list[tuple[str, ...]]:
    return [call for call in git.calls if call[0] == "merge_branch"]


def push_calls(git: FakeGitService) -> list[tuple[str, ...]]:
    return [call for call in git.calls if call[0] == "push"]


# ---------------------------------------------------------------------------
# Work-ref roles
# ---------------------------------------------------------------------------


def test_work_ref_role_members_and_values_verbatim() -> None:
    """The wire values are pinned: a rename is a migration, not an edit."""
    assert [(member.name, member.value) for member in WorkRefRole] == [
        ("DELIVERABLE", "deliverable"),
        ("ITERATION", "iteration"),
        ("RECOVERY", "recovery"),
        ("BEST_ITERATION", "best_iteration"),
        ("INTEGRATION", "integration"),
    ]


async def test_a_second_deliverable_ref_for_one_issue_raises() -> None:
    """Never a silent replacement: it would move every dependent lane's base."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("B-1")])
    await tracker.record_work_ref(ref=ref("B-1", "feature-a"))
    with pytest.raises(DuplicateWorkRefError) as excinfo:
        await tracker.record_work_ref(ref=ref("B-1", "feature-b"))
    assert excinfo.value.existing_branch == "feature-a"
    assert excinfo.value.offered_branch == "feature-b"


async def test_a_second_ref_at_another_role_is_accepted() -> None:
    """One issue carries several refs; only DELIVERABLE is exclusive."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("B-1")])
    await tracker.record_work_ref(ref=ref("B-1", "feature-a"))
    await tracker.record_work_ref(
        ref=ref("B-1", "feature-a-ralph", role=WorkRefRole.ITERATION),
    )
    assert len(await tracker.work_refs(issue_key="B-1")) == 2


# ---------------------------------------------------------------------------
# The three arms
# ---------------------------------------------------------------------------


async def test_no_blockers_resolves_to_the_scopes_configured_trunk() -> None:
    tracker = FakeTrackerPort(issues=[make_tracker_issue(LANE)])
    git = FakeGitService()
    spec = await resolve(tracker, git)
    assert spec.base_branch == CONFIGURED_TRUNK
    assert spec.inputs == ()
    assert spec.base_role is None


async def test_neither_shipped_main_default_is_consulted_on_the_trunk_arm() -> None:
    """AC: both ``"main"`` literal defaults asserted UNUSED.

    Each default is read from the shipped code rather than written here, so
    this stays true if either literal changes; what is asserted is that the
    resolved base is the CONFIGURED trunk and neither default.
    """
    request_default = WorkflowRequest.model_fields["base_branch"].default
    service_defaults = AgentService.stream_workflow.__kwdefaults__
    assert request_default == "main"
    assert service_defaults["base_branch"] == "main"

    tracker = FakeTrackerPort(issues=[make_tracker_issue(LANE)])
    spec = await resolve(tracker, FakeGitService())
    assert spec.base_branch != request_default
    assert spec.base_branch == CONFIGURED_TRUNK


async def test_one_blocker_resolves_to_that_blockers_deliverable_ref() -> None:
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1"]),
            make_tracker_issue("B-1"),
        ],
        recorded_work_refs={"B-1": [ref("B-1", "feature-b1")]},
    )
    git = FakeGitService()
    spec = await resolve(tracker, git)
    assert spec.base_branch == "feature-b1"
    assert spec.base_role is WorkRefRole.DELIVERABLE
    assert merge_calls(git) == []
    assert tracker.recorded_work_refs.get(LANE, []) == []


async def test_a_chain_of_three_blockers_resolves_to_the_tip() -> None:
    """Many blockers, singleton frontier — the same arm the single case takes."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1", "B-2", "B-3"]),
            make_tracker_issue("B-1"),
            make_tracker_issue("B-2"),
            make_tracker_issue("B-3"),
        ],
        recorded_work_refs={
            "B-1": [ref("B-1", "feature-b1")],
            "B-2": [ref("B-2", "feature-b2")],
            "B-3": [ref("B-3", "feature-b3")],
        },
    )
    git = FakeGitService(
        ancestor_pairs={
            ("feature-b1", "feature-b2"),
            ("feature-b2", "feature-b3"),
            ("feature-b1", "feature-b3"),
        },
    )
    spec = await resolve(tracker, git)
    assert spec.base_branch == "feature-b3"
    assert spec.base_role is WorkRefRole.DELIVERABLE
    assert merge_calls(git) == []
    assert tracker.recorded_work_refs.get(LANE, []) == []


async def test_fan_in_constructs_and_records_an_integration_ref() -> None:
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-2", "B-1"]),
            make_tracker_issue("B-1"),
            make_tracker_issue("B-2"),
        ],
        recorded_work_refs={
            "B-1": [ref("B-1", "feature-b1")],
            "B-2": [ref("B-2", "feature-b2")],
        },
    )
    git = FakeGitService()
    spec = await resolve(tracker, git)

    assert spec.base_role is WorkRefRole.INTEGRATION
    assert spec.inputs == (
        BaseInput(blocker_issue_id="B-1", branch="feature-b1", sha="sha-feature-b1"),
        BaseInput(blocker_issue_id="B-2", branch="feature-b2", sha="sha-feature-b2"),
    )
    # Branched from the first input; the remainder merged in that order and
    # with no other input.
    assert merge_calls(git) == [("merge_branch", INTEGRATION_WORKSPACE, "feature-b2")]
    recorded = tracker.recorded_work_refs[LANE]
    assert [(r.role, r.branch) for r in recorded] == [
        (WorkRefRole.INTEGRATION, spec.base_branch),
    ]


async def test_the_same_blockers_in_reversed_read_order_yield_one_base() -> None:
    """Order determinism: byte-identical spec and the same merge sequence."""
    specs = []
    sequences = []
    for order in (["B-1", "B-2", "B-3"], ["B-3", "B-2", "B-1"]):
        tracker = FakeTrackerPort(
            issues=[
                make_tracker_issue(LANE, blocked_by=order),
                *[make_tracker_issue(key) for key in order],
            ],
            recorded_work_refs={
                key: [ref(key, f"feature-{key.lower()}")] for key in order
            },
        )
        git = FakeGitService()
        specs.append(await resolve(tracker, git))
        sequences.append(merge_calls(git))

    assert specs[0].model_dump_json() == specs[1].model_dump_json()
    assert sequences[0] == sequences[1]


async def test_a_redundant_edge_reduces_to_the_descendant_alone() -> None:
    """The redundant edge changes neither the base nor the BaseSpec."""
    without = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-2"]),
            make_tracker_issue("B-2"),
        ],
        recorded_work_refs={"B-2": [ref("B-2", "feature-b2")]},
    )
    baseline = await resolve(without, FakeGitService())

    with_edge = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1", "B-2"]),
            make_tracker_issue("B-1"),
            make_tracker_issue("B-2"),
        ],
        recorded_work_refs={
            "B-1": [ref("B-1", "feature-b1")],
            "B-2": [ref("B-2", "feature-b2")],
        },
    )
    git = FakeGitService(ancestor_pairs={("feature-b1", "feature-b2")})
    reduced = await resolve(with_edge, git)

    assert reduced.base_branch == "feature-b2"
    assert reduced.model_dump_json() == baseline.model_dump_json()
    assert merge_calls(git) == []


# ---------------------------------------------------------------------------
# Where a deliverable ref comes from (KOD-149)
# ---------------------------------------------------------------------------


def lifecycle(tracker: FakeTrackerPort) -> TrackerLifecycleWriter:
    """The shipped lifecycle writer — the only producer of DELIVERABLE refs."""
    return TrackerLifecycleWriter(
        tracker=tracker,
        gate=PassThroughGate(),
        clock=lambda: FIXTURE_EPOCH,
    )


def blocked_lane() -> FakeTrackerPort:
    """LANE blocked by B-1, and nothing recorded against either."""
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1"]),
            make_tracker_issue("B-1"),
        ],
    )


async def test_a_lane_whose_blocker_delivered_nothing_still_raises() -> None:
    """The state the whole board was in: no process wrote a deliverable ref.

    ``record_work_ref`` had one caller under ``src`` and it wrote
    INTEGRATION refs, so this raise was not a fixture's omission — it was
    every issue with a blocker, on every pass.
    """
    with pytest.raises(BaseResolutionError) as excinfo:
        await resolve(blocked_lane(), FakeGitService())
    assert excinfo.value.blocker_issue_ids == ("B-1",)


async def test_the_blockers_pull_request_is_what_makes_its_lane_resolvable() -> None:
    """The regression, over one tracker: the same fixture now resolves.

    The ref is written by the lifecycle arm rather than seeded, so a writer
    that stopped recording it fails here and not only in its own suite.
    """
    tracker = blocked_lane()
    await lifecycle(tracker).on_pull_request(
        issue_key="B-1",
        feature_branch="kodezart/b-1-the-blockers-lane",
        feature_tip_sha="c" * 40,
        delivered=True,
    )

    spec = await resolve(tracker, FakeGitService())

    assert spec.base_branch == "kodezart/b-1-the-blockers-lane"
    assert spec.base_role is WorkRefRole.DELIVERABLE
    assert spec.inputs == (
        BaseInput(
            blocker_issue_id="B-1",
            branch="kodezart/b-1-the-blockers-lane",
            sha="c" * 40,
        ),
    )


# ---------------------------------------------------------------------------
# Blocker states — total, with no silent fallback
# ---------------------------------------------------------------------------


async def test_a_blocker_with_no_ref_resolves_to_its_nearest_ancestor() -> None:
    """Work riding another issue's pull request records no ref of its own."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-CHILD"]),
            make_tracker_issue("B-CHILD", parent_key="B-PARENT"),
            make_tracker_issue("B-PARENT"),
        ],
        recorded_work_refs={"B-PARENT": [ref("B-PARENT", "feature-parent")]},
    )
    spec = await resolve(tracker, FakeGitService())
    assert spec.base_branch == "feature-parent"


async def test_a_blocker_whose_ancestors_carry_no_ref_raises() -> None:
    """The paired negative: trunk is NOT substituted."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-CHILD"]),
            make_tracker_issue("B-CHILD", parent_key="B-PARENT"),
            make_tracker_issue("B-PARENT"),
        ],
    )
    with pytest.raises(BaseResolutionError) as excinfo:
        await resolve(tracker, FakeGitService())
    assert excinfo.value.blocker_issue_ids == ("B-CHILD",)
    assert CONFIGURED_TRUNK not in str(excinfo.value)


async def test_a_ref_absent_from_the_remote_raises() -> None:
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1"]),
            make_tracker_issue("B-1"),
        ],
        recorded_work_refs={"B-1": [ref("B-1", "feature-b1")]},
    )
    git = FakeGitService(remote_branch_shas={"feature-b1": None})
    with pytest.raises(BaseResolutionError) as excinfo:
        await resolve(tracker, git)
    assert excinfo.value.branches == ("feature-b1",)
    assert merge_calls(git) == []
    assert push_calls(git) == []


async def test_a_ref_that_was_never_pushed_raises() -> None:
    """``pushed_head_sha is None`` is a refusal, not a falsy branch."""
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1"]),
            make_tracker_issue("B-1"),
        ],
        recorded_work_refs={"B-1": [ref("B-1", "feature-b1", sha=None)]},
    )
    tracker.recorded_work_refs["B-1"] = [
        WorkRef(
            issue_id="B-1",
            role=WorkRefRole.DELIVERABLE,
            branch="feature-b1",
            pushed_head_sha=None,
            recorded_at=FIXTURE_EPOCH,
        ),
    ]
    git = FakeGitService()
    with pytest.raises(BaseResolutionError) as excinfo:
        await resolve(tracker, git)
    assert excinfo.value.branches == ("feature-b1",)
    assert push_calls(git) == []


async def test_a_textual_conflict_names_both_refs_and_the_paths() -> None:
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=["B-1", "B-2"]),
            make_tracker_issue("B-1"),
            make_tracker_issue("B-2"),
        ],
        recorded_work_refs={
            "B-1": [ref("B-1", "feature-b1")],
            "B-2": [ref("B-2", "feature-b2")],
        },
    )
    git = FakeGitService(
        merge_conflicts={"feature-b2": ("src/a.py", "src/b.py")},
    )
    with pytest.raises(BaseIntegrationConflictError) as excinfo:
        await resolve(tracker, git)

    assert excinfo.value.branches == ("feature-b1", "feature-b2")
    assert excinfo.value.paths == ("src/a.py", "src/b.py")
    assert push_calls(git) == []
    assert tracker.recorded_work_refs.get(LANE, []) == []
    assert CONFIGURED_TRUNK not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Degenerate-not-special, and merge-state independence
# ---------------------------------------------------------------------------


def test_the_resolver_has_exactly_one_implementation() -> None:
    """One rule.  A second entry point is a second rule that will diverge."""
    tree = ast.parse(RESOLVER_MODULE.read_text(encoding="utf-8"))
    public = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    ]
    assert public == ["resolve_base"]

    # Walked rather than grepped, at every nesting depth: a second rule is
    # far likelier to arrive as a method on a service than as a second
    # module-level function, and an anchored text match cannot see one.
    definitions = [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "resolve_base"
    ]
    assert len(definitions) == 1, definitions


def test_the_resolver_takes_no_branch_on_the_number_of_blockers() -> None:
    """No size is compared against a number anywhere in the rule.

    The three combine arms are reached by destructuring, so a shortcut that
    special-cased one blocker would have to introduce a comparison — which
    is exactly what this rejects.
    """
    tree = ast.parse(RESOLVER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "len", ast.unparse(node)


def test_the_resolver_carries_no_numeric_or_branch_literal() -> None:
    tree = ast.parse(RESOLVER_MODULE.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings:
            continue
        if isinstance(node.value, bool):
            continue
        assert not isinstance(node.value, int | float | complex), ast.unparse(node)
        assert not isinstance(node.value, str), ast.unparse(node)


def _lines_traversed(
    inputs: list[BaseInput], containment: list[tuple[str, str]]
) -> set[int]:
    """Every line of the resolver module executed for one input set."""
    seen: set[int] = set()
    target = str(RESOLVER_MODULE)

    def tracer(frame: FrameType, event: str, _arg: object) -> object:
        if frame.f_code.co_filename != target:
            return None
        if event == "line":
            seen.add(frame.f_lineno)
        return tracer

    sys.settrace(tracer)
    try:
        resolve_base(
            issue_id=LANE,
            blocker_inputs=inputs,
            containment=containment,
            trunk=CONFIGURED_TRUNK,
        )
    finally:
        sys.settrace(None)
    return seen


def test_one_blocker_and_a_chain_of_three_traverse_the_same_code() -> None:
    """The call-path assertion a single-blocker shortcut fails, and only it."""
    single = _lines_traversed(
        [BaseInput(blocker_issue_id="B-1", branch="feature-b1", sha="s1")],
        [],
    )
    chain = _lines_traversed(
        [
            BaseInput(blocker_issue_id="B-1", branch="feature-b1", sha="s1"),
            BaseInput(blocker_issue_id="B-2", branch="feature-b2", sha="s2"),
            BaseInput(blocker_issue_id="B-3", branch="feature-b3", sha="s3"),
        ],
        [
            ("feature-b1", "feature-b2"),
            ("feature-b2", "feature-b3"),
            ("feature-b1", "feature-b3"),
        ],
    )
    assert single == chain


def test_the_pr_port_and_its_double_expose_no_merge(
    pr_creator: FakePRCreator,
) -> None:
    """A merge call on a pull request cannot type-check against the port."""
    from kodezart.core import protocols

    surface = {name for name in vars(protocols.PRCreator) if not name.startswith("_")}
    assert surface == {"create_pr", "comment_on_pr"}
    assert [name for name in dir(pr_creator) if "merge" in name] == []


@pytest.fixture
def pr_creator() -> FakePRCreator:
    return FakePRCreator()


#: Every method the forge ports answer to: ``PRCreator``, ``CIMonitor``,
#: ``DeliveryProbe`` and the visibility resolver.  A collaborator carrying
#: any of them is a pull-request-state read waiting to be added.
_FORGE_METHODS: frozenset[str] = frozenset(
    {
        "create_pr",
        "comment_on_pr",
        "wait_for_checks",
        "open_delivery_exists",
        "resolve_visibility",
    },
)

#: The two modules the rule and its I/O half live in.
_RESOLUTION_MODULES: tuple[Path, ...] = (
    RESOLVER_MODULE,
    REPO_ROOT / "src" / "kodezart" / "services" / "base_resolver.py",
)


def _is_forge_shaped(value: object) -> bool:
    return any(hasattr(value, name) for name in _FORGE_METHODS)


def test_the_forge_predicate_recognises_the_forge_double(
    pr_creator: FakePRCreator,
) -> None:
    """Guards the two tests below: a predicate that never fires proves nothing."""
    assert _is_forge_shaped(pr_creator)


def test_the_resolver_holds_no_collaborator_that_could_read_forge_state(
    pr_creator: FakePRCreator,
) -> None:
    """Merge-state independence, asserted where it can actually fail.

    Under the standing ruling an open unmerged pull request is the steady
    state of every finished lane, so pull-request state is not an input to
    base resolution.  The check is over the resolver a production call site
    builds — a resolver that grew a forge collaborator fails here, whatever
    it then did with it.
    """
    subject = resolver(FakeTrackerPort(), FakeGitService())

    assert [value for value in vars(subject).values() if _is_forge_shaped(value)] == []
    assert _is_forge_shaped(pr_creator)


def test_neither_resolution_module_can_reach_the_forge_at_all() -> None:
    """The rule is pure and its I/O half reads the tracker and git, nothing else."""
    for module in _RESOLUTION_MODULES:
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        assert not [name for name in imported if "github" in name or "forge" in name], (
            module.name
        )
        for name in _FORGE_METHODS:
            assert name not in source, (module.name, name)


# ---------------------------------------------------------------------------
# D2 — nothing derives an issue identity, a role or a parent from a branch name
# ---------------------------------------------------------------------------

SRC = REPO_ROOT / "src" / "kodezart"
BRANCH_NAMING_MODULE = SRC / "types" / "domain" / "branch.py"

#: Everything that inspects the INTERNAL structure of a string.  A derived
#: identity, role or parent is one of these applied to a name.
_PARSING_CALLS = frozenset(
    {
        "split",
        "rsplit",
        "partition",
        "rpartition",
        "removeprefix",
        "removesuffix",
        "startswith",
        "endswith",
        "index",
        "find",
        "match",
        "search",
        "fullmatch",
        "group",
    },
)

#: The three things D2 forbids deriving.  Names, keywords and attributes
#: that mean "which issue", "which role" and "whose parent".
_DERIVED_NAMES = frozenset(
    {
        "issue_id",
        "issue_key",
        "issue_ids",
        "issue_keys",
        "role",
        "parent",
        "parent_key",
    },
)


def _names_a_ref(node: ast.expr) -> bool:
    """Whether *node* names a value that is a ref name."""
    if isinstance(node, ast.Name):
        return "branch" in node.id or node.id.endswith("ref")
    if isinstance(node, ast.Attribute):
        return "branch" in node.attr or node.attr.endswith("ref")
    return False


def _parses_a_ref_name(expression: ast.expr) -> bool:
    """Whether *expression* takes a REF NAME apart.

    Parsing a tracker payload is not what D2 forbids — that is the read it
    mandates.  What it forbids is recovering the same facts from the name
    of a branch, which is a second source of truth with no author and no
    history behind it.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _PARSING_CALLS
        and (
            _names_a_ref(node.func.value) or any(_names_a_ref(arg) for arg in node.args)
        )
        for node in ast.walk(expression)
    )


def _derivations(tree: ast.AST) -> Iterator[ast.expr]:
    """Every expression bound to an identity-, role- or parent-shaped name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _DERIVED_NAMES:
                    yield node.value
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr):
            target = node.target
            if isinstance(target, ast.Name) and target.id in _DERIVED_NAMES:
                if node.value is not None:
                    yield node.value
        elif isinstance(node, ast.keyword):
            if node.arg in _DERIVED_NAMES:
                yield node.value


def test_nothing_derives_an_issue_identity_role_or_parent_from_a_name() -> None:
    """D2, stated as the thing it forbids.

    The association between an issue and its refs is tracker-side and is
    read through the port, so no value meaning "which issue", "which role"
    or "whose parent" may be computed by taking a string apart.  A ref name
    is opaque everywhere except the module that owns the convention.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == BRANCH_NAMING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}: {ast.unparse(expression)}"
            for expression in _derivations(tree)
            if _parses_a_ref_name(expression)
        )
    assert offenders == []


def test_the_branch_naming_convention_lives_in_exactly_one_module() -> None:
    """The infixes that give a ref name its shape are code in one module only.

    Prose is not the concern — a docstring may describe the convention.
    What may not exist twice is the LITERAL a name is built from, because a
    second copy is a second convention that will drift from the first.
    """
    infixes = ("-backup-", "-integration-")
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == BRANCH_NAMING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node,
                ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}: {node.value!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(infix in node.value for infix in infixes)
        )
    assert offenders == []


def test_no_pull_request_merge_call_exists_under_src() -> None:
    """Merging a pull request is a human decision kodezart never performs.

    Constructing a base IS a git operation on branches the run owns, and
    ``GitService.merge_branch`` is how D4 specifies it; what may not exist
    is a call that merges a PULL REQUEST, or a read of one's merge state
    used to decide whether it may.
    """
    forbidden = (
        "merge_pull_request",
        "merge_pr",
        "pulls/merge",
        "pr merge",
        "merged_at",
        "merge_commit_sha",
    )
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {token}"
        for path in sorted(SRC.rglob("*.py"))
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
