"""The shipped scope operation file, walked and booted as an operator has it.

`tests/integration/test_scope_runtime.py` is about what a walk does; this
module is about whether the file a person copies can be walked at all. Every
member the walk needs and the file lacks shows up here as a lane failure, which
is how the file's contents are settled rather than guessed.
"""

from pathlib import Path

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import criteria_stage_label_key
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.integration.test_scope_runtime import (
    ORIGIN,
    WalkRepos,
    board,
    bounded_walk,
    lane_failures,
    lane_record,
    resumable,
)

#: The file a scope operator copies. Loaded rather than restated: a test
#: written against its own copy of these names would pass over a file nobody
#: could boot.
SCOPE_EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "operation.scope.toml"


def shipped():
    return load_operation_config(SCOPE_EXAMPLE)


async def test_the_shipped_scope_config_walks_one_lane_to_a_crossed_off_criterion():
    """One lane, from the shipped file's own names, all the way to Done.

    The board is labelled with the criteria-stage key the file's own mandate
    table names and answers under the file's own marker prefixes, so nothing
    here stands on a constant this module chose. A purpose the walk resolves
    and the file does not declare would be contained at the lane boundary and
    named in `failed_lanes`, which is why that assertion comes first — it is
    how this file's marker list was settled rather than guessed.

    Over the forge-less origin the module's other walks use, because the shared
    forge double answers for one hardcoded address and pointing a shipped
    example at that address would be a worse file. Nothing this case is about
    is decided by the origin: the labels, the markers and the states are.
    """
    loaded = shipped()
    repos = WalkRepos()
    port = board(lanes=("A",), operation=loaded)
    harness = resumable(repos=repos, port=port, operation=loaded, origin=ORIGIN)
    events = await bounded_walk(harness, origin=ORIGIN)
    assert lane_failures(events) == ()
    criterion = port.issues["A/check"]
    assert criterion.state_kind is WorkflowStateKind.COMPLETED
    assert criterion.state_name == LifecycleStage.DONE.value
    # The cross-off carries the sha the lane's own branch stands at, read back
    # through the codec rather than off the prose.
    record = await lane_record(port, "A", operation=loaded)
    assert parse_criterion_evidence(criterion.body).graded_sha == record.head_sha
    # The record was written under the prefix the SHIPPED file declares, which
    # is what a second process would go looking for.
    assert record.branch
    assert [
        comment.body
        for comment in port.comments
        if comment.issue_key == "A"
        and comment.body.startswith(f"[{loaded.marker_prefixes['run_state']}:")
    ]


def test_the_shipped_file_names_the_criteria_stage_the_adapter_is_built_with():
    """One answer, from the builder's own function: a lane fires on this label."""
    loaded = shipped()
    row = next(
        row
        for row in loaded.resolve_organize_mandates()
        if row.role.marks_execution_stage
    )
    assert (
        criteria_stage_label_key(loaded) == row.spec.terminal_marker_key.split(".")[1]
    )
