"""A union result cannot detach remediation from the observation it answers."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.check_chain import CheckChainResult, CheckStepOutput
from kodezart.types.domain.union import (
    UnionCompositionResult,
    UnionMergeConflict,
    UnionRemediationEntry,
)
from tests.domain.test_union import observation


def conflict(lane="a", paths=("api.py",)):
    return UnionMergeConflict(lane_key=lane, paths=paths)


def merge_entry(lane="a", paths=("api.py",)):
    return UnionRemediationEntry(
        root_step_names=(), cascade_step_names=(), merge_conflict=conflict(lane, paths)
    )


def check_entry(roots=("gate",), cascades=("downstream",)):
    return UnionRemediationEntry(root_step_names=roots, cascade_step_names=cascades)


def checks(names=("gate", "downstream")):
    return CheckChainResult(
        failed_step_names=frozenset(names),
        step_outputs=tuple(
            CheckStepOutput(name=name, output="observed", exit_code=1, timed_out=False)
            for name in names
        ),
    )


@pytest.mark.parametrize("entry", [merge_entry(), check_entry()])
def test_entry_round_trip_preserves_one_cause(entry):
    assert UnionRemediationEntry.model_validate_json(entry.model_dump_json()) == entry
    with pytest.raises(ValidationError):
        entry.merge_conflict = conflict("z")


@pytest.mark.parametrize(
    "fields",
    [
        {"root_step_names": (), "cascade_step_names": ()},
        {"root_step_names": (), "cascade_step_names": ("gate",)},
        {"root_step_names": ("gate", "gate"), "cascade_step_names": ()},
        {"root_step_names": ("gate",), "cascade_step_names": ("gate",)},
        {"root_step_names": ("gate",), "cascade_step_names": ("after", "after")},
        {
            "root_step_names": ("gate",),
            "cascade_step_names": (),
            "merge_conflict": conflict(),
        },
        {
            "root_step_names": (),
            "cascade_step_names": ("gate",),
            "merge_conflict": conflict(),
        },
    ],
)
def test_empty_duplicated_or_mixed_cause_refuses(fields):
    with pytest.raises(ValidationError):
        UnionRemediationEntry.model_validate(fields)


@pytest.mark.parametrize(
    "entry",
    [
        None,
        check_entry(),
        merge_entry("z"),
        merge_entry(paths=("other.py",)),
        [merge_entry(), merge_entry()],
    ],
)
def test_conflict_result_requires_its_single_actual_lane_and_paths(entry):
    with pytest.raises(ValidationError):
        UnionCompositionResult(
            **observation().model_dump(),
            checks=None,
            merge_conflict=conflict(),
            remediation=entry,
        )


@pytest.mark.parametrize(
    "entry",
    [
        None,
        merge_entry(),
        check_entry(cascades=()),
        check_entry(cascades=("other",)),
        check_entry(cascades=("downstream", "extra")),
    ],
)
def test_check_red_requires_exact_observed_failure_partition(entry):
    with pytest.raises(ValidationError):
        UnionCompositionResult(
            **observation().model_dump(), checks=checks(), remediation=entry
        )


@pytest.mark.parametrize("entry", [merge_entry(), check_entry()])
def test_green_has_no_remediation(entry):
    with pytest.raises(ValidationError):
        UnionCompositionResult(
            **observation().model_dump(), checks=checks(()), remediation=entry
        )
