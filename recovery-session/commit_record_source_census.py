"""Read-only source census at the reviewed L4 proposal pin."""

import ast
import subprocess

ROOT = "/private/tmp/kodezart-v03-recovery-integration"
SHA = "d2c6fceab762191d4e40b23c8cd349ef476e4b12"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def read(path: str) -> str:
    return git("show", f"{SHA}:{path}")


calls = []
for path in git("ls-tree", "-r", "--name-only", SHA, "src").splitlines():
    if path.endswith(".py"):
        for node in ast.walk(ast.parse(read(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "LaneRunState"
            ):
                calls.append((path, node.lineno))
print(f"Source SHA: {SHA}")
print(f"LaneRunState constructor calls in src: {calls}")
for path, names in (
    ("src/kodezart/types/domain/trajectory.py", ["IterationRecord"]),
    ("src/kodezart/types/domain/persist.py", ["PersistResult"]),
    ("src/kodezart/domain/run_event_stream.py", ["LaneRunEvent"]),
    ("src/kodezart/types/domain/workflow.py", ["WorkflowContext", "RalphLoopContext"]),
):
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.ClassDef) and node.name in names:
            fields = [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            ]
            print(f"{path}:{node.lineno} {node.name} fields = {fields}")
print("Characterization only; this is not a completed first-push/tenth-commit test.")
