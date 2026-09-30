"""The facts one measured composition is stated as, over shipped values.

Built the way ``tests/domain/test_union.py`` builds them, so the arithmetic is
read off a real result and not off a shape written to suit the function.
"""

from kodezart.domain.union_facts import union_facts
from kodezart.types.domain.check_chain import CheckChainResult, CheckStepOutput
from kodezart.types.domain.union import (
    UnionCompositionResult,
    UnionLaneHead,
    UnionRemediationEntry,
)

GREEN_CHECKS = CheckChainResult(
    failed_step_names=frozenset(),
    step_outputs=(
        CheckStepOutput(
            name="gate", output="every step passed", exit_code=0, timed_out=False
        ),
    ),
)

RED_CHECKS = CheckChainResult(
    failed_step_names=frozenset({"gate"}),
    step_outputs=(
        CheckStepOutput(
            name="gate", output="the gate said no", exit_code=1, timed_out=False
        ),
    ),
)


def head(key: str, sha: str) -> UnionLaneHead:
    return UnionLaneHead(lane_key=key, branch=f"work/{key}", head_sha=sha)


def result(**changes: object) -> UnionCompositionResult:
    data: dict[str, object] = {
        "scope_key": "scope",
        "repository_url": "file:///repo",
        "base_sha": "b" * 40,
        "lane_heads": (head("z", "a" * 40), head("a", "c" * 40)),
        "scratch_path": "/scratch/discarded",
        "scratch_sha": "d" * 40,
        "checks": GREEN_CHECKS,
    }
    data.update(changes)
    return UnionCompositionResult(**data)


def test_every_measured_head_is_named_once_in_composition_order() -> None:
    """One entry per lane, carrying the head that lane was measured at."""
    measured = result()

    stated = union_facts(measured)

    assert stated["heads"] == (f"z:{'a' * 40}", f"a:{'c' * 40}")
    assert stated["lanes"] == 2
    assert stated["scratch_sha"] == "d" * 40
    # The order the composition took them, which the planner ranked.
    assert (
        tuple(entry.split(":")[0] for entry in stated["heads"])
        == measured.composition_order
    )


def test_a_green_states_no_remediation_and_a_red_states_its_detail() -> None:
    """The verdict and its repair, both read off the result rather than named."""
    green = union_facts(result())
    red = union_facts(
        result(
            checks=RED_CHECKS,
            remediation=UnionRemediationEntry(
                root_step_names=("gate",), cascade_step_names=()
            ),
        )
    )

    assert green["composition"] == "green"
    assert green["remediation"] is None
    assert red["composition"] == "red"
    assert (
        red["remediation"] == "Repair union check roots: gate. Cascading checks: none."
    )
