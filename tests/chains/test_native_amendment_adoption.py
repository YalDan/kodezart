"""Every actual branch-writing dispatch adopts the compiled precommit owner."""

import ast
from pathlib import Path

import pytest

from kodezart.chains.native_amendment import NativeAmendmentGraph
from kodezart.services.native_amendments import NATIVE_WRITING_STAGES
from kodezart.types.domain.prompts import PromptKey

ROOT = Path(__file__).resolve().parents[2] / "src" / "kodezart"
# All normal iterations and review/CI remediation reentry use this same node.
WRITING_STAGES = (
    ("chains/ralph_loop.py", "RalphLoop", "_execute_node", PromptKey.IMPLEMENTATION),
)


def dispatches(sources):
    found = {}
    for path, source in sources.items():
        tree = ast.parse(source)
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for call in ast.walk(tree):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Attribute)
                or call.func.attr != "stream_workflow"
            ):
                continue
            ancestors = []
            node = call
            while node in parents:
                node = parents[node]
                ancestors.append(node)
            function = next(
                (
                    node
                    for node in ancestors
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                None,
            )
            owner = next(
                (node for node in ancestors if isinstance(node, ast.ClassDef)), None
            )
            address = (
                path,
                None if owner is None else owner.name,
                None if function is None else function.name,
            )
            if address == ("api/v1/endpoints/agent.py", None, "generate"):
                # This exact HTTP adapter calls the queue handler, not AgentRunner.
                assert ast.unparse(call.func.value) == "handler"
                continue
            if address not in found:
                found[address] = (function, [])
            found[address][1].append(call)
    return found


def require_adoption(sources):
    actual = dispatches(sources)
    assert set(actual) == {
        (path, owner, node) for path, owner, node, _ in WRITING_STAGES
    }
    assert {role for _, _, _, role in WRITING_STAGES} == NATIVE_WRITING_STAGES
    for path, owner, node, role in WRITING_STAGES:
        function, calls = actual[path, owner, node]
        assert len(calls) == 1
        guard = next(
            keyword.value
            for keyword in calls[0].keywords
            if keyword.arg == "native_guard"
        )
        assert isinstance(guard, ast.Name)
        constructors = [
            assignment
            for assignment in ast.walk(function)
            if isinstance(assignment, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == guard.id
                for target in assignment.targets
            )
            and isinstance(assignment.value, ast.Call)
            and isinstance(assignment.value.func, ast.Attribute)
            and assignment.value.func.attr == "for_writer"
        ]
        assert len(constructors) == 1
        stage = next(
            keyword.value
            for keyword in constructors[0].value.keywords
            if keyword.arg == "stage"
        )
        assert ast.unparse(stage) == f"PromptKey.{role.name}"


def sources():
    return {
        path.relative_to(ROOT).as_posix(): path.read_text()
        for path in ROOT.rglob("*.py")
    }


def test_every_actual_branch_writer_uses_the_native_precommit_owner():
    require_adoption(sources())


@pytest.mark.parametrize("mutation", ["bypass", "new_writer", "new_function"])
def test_stage_table_rejects_an_unguarded_commit_dispatch(mutation):
    changed = sources()
    if mutation == "bypass":
        changed["chains/ralph_loop.py"] = changed["chains/ralph_loop.py"].replace(
            "native_guard=native_guard", "native_guard=None"
        )
    elif mutation == "new_function":
        changed["chains/new_writer.py"] = (
            "async def execute(runner):\n"
            "    await runner.stream_workflow(prompt='write without guard')\n"
        )
    else:
        changed["chains/new_writer.py"] = (
            "class NewWriter:\n    async def execute(self):\n"
            "        await self.runner.stream_workflow(prompt='write without guard')\n"
        )
    with pytest.raises(AssertionError):
        require_adoption(changed)


def test_real_precommit_graph_has_no_canonical_write_route_without_judgment():
    # No collaborator executes for this structural graph contract.
    graph = NativeAmendmentGraph(actions=None).graph.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "judge",
        "canonical_write_back",
        "complete",
        "__end__",
    }
    incoming = {
        edge.source for edge in graph.edges if edge.target == "canonical_write_back"
    }
    assert incoming == {"judge"}
    assert any(
        edge.source == "canonical_write_back" and edge.target == "judge"
        for edge in graph.edges
    )
