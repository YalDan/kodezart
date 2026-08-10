"""Every KOD-53 criterion is cited on the demonstration that shows it (KOD-53 R6).

R6 requires each of this lane's criteria to carry a ``KOD-53/AC-n`` marker on
the test that demonstrates it, in the lane-wide numbering, and forbids a
marker that resolves to a live lane id it does not name.  The second clause is
the one R6 calls strictly worse than silence, and counting markers does not
mechanize it: re-pointing the marker over the trunk-arm base-selection test
from ``AC-22`` to ``AC-4`` — exactly the defect R6 was written for — left a
count-only guard green, because ``AC-22`` was still cited elsewhere and
``AC-4`` is a live id.

So the map is held here as data, one record per (criterion, demonstration),
and the tree is asserted equal to it in BOTH directions.  A marker that
vanishes, moves onto another test, or is re-pointed at another id changes the
derived set and reddens, and the failure names the record that moved.  This is
also what stops the next grading re-deriving the map by mutation, as two
consecutive gradings had to.

What the map does not do, said plainly so it is not read for more: it does not
judge whether the named test demonstrates the criterion.  That judgement is a
reader's, taken against each member issue's Verification section and recorded
here once; the guard holds the tree to what was recorded.

This grades no criterion.  KOD-53 R1 and R2 forbid authoring a lane-level
acceptance criterion, and a pointer to a demonstration is not one: it asserts
nothing about behaviour and cannot substitute for a demonstration.
"""

import re
from pathlib import Path

#: KOD-66 contributes 12 Verification bullets, KOD-69 2, KOD-71 3, KOD-11 3
#: and KOD-36 7 — the lane-wide numbering runs AC-1..AC-27 in that order.
LANE_CRITERION_COUNT = 27

TESTS_ROOT = Path(__file__).resolve().parent
MAP_FILE = Path(__file__).resolve().name
CITATION = re.compile(r"KOD-53/AC-(\d+)")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")

#: The criterion -> demonstration map: ``(criterion, path under tests/, test)``.
#: A marker binds to the nearest test function — the one enclosing it when the
#: marker is indented, which is a docstring or a comment inside a test, and the
#: first one below it when the marker starts at column 0, which is a section
#: header.  One marker may name several criteria and one criterion may be
#: demonstrated by several tests, so this is a relation rather than a mapping.
DEMONSTRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "domain/test_criteria_feasibility.py", "test_classification_table"),
    (
        1,
        "domain/test_criteria_feasibility.py",
        "test_criterion_side_repair_without_a_refutation_raises",
    ),
    (
        1,
        "domain/test_criteria_feasibility.py",
        "test_the_two_observation_classes_are_told_apart_by_their_own_evidence",
    ),
    (
        2,
        "chains/test_criteria_validation.py",
        "test_an_unsatisfiable_conjunction_regenerates_then_halts",
    ),
    (
        2,
        "domain/test_criteria_feasibility.py",
        "test_jointly_unsatisfiable_set_names_the_minimal_conflicting_subset",
    ),
    (
        2,
        "domain/test_criteria_feasibility.py",
        "test_the_named_subset_is_the_smallest_then_the_lexicographically_first",
    ),
    (
        3,
        "chains/test_criteria_validation.py",
        "test_permanently_infeasible_criteria_halt_before_the_loop",
    ),
    (
        3,
        "chains/test_criteria_validation.py",
        "test_regeneration_bound_is_a_named_config_field",
    ),
    (
        4,
        "chains/test_criteria_validation.py",
        "test_permanently_infeasible_criteria_halt_before_the_loop",
    ),
    (
        5,
        "chains/test_criteria_validation.py",
        "test_the_persisted_artifact_carries_ids_verdicts_and_evidence",
    ),
    (5, "domain/test_criteria_feasibility.py", "test_ids_are_minted_in_emission_order"),
    (
        5,
        "domain/test_criterion_identity.py",
        "test_the_identity_is_a_new_type_over_str",
    ),
    (
        6,
        "chains/test_criteria_validation.py",
        "test_a_missing_validator_finding_fails_the_run_closed",
    ),
    (
        6,
        "domain/test_criteria_feasibility.py",
        "test_missing_finding_is_fail_closed_and_names_the_id",
    ),
    (
        7,
        "chains/test_criteria_validation.py",
        "test_unverifiable_only_set_neither_regenerates_nor_halts",
    ),
    (
        7,
        "domain/test_criteria_feasibility.py",
        "test_fault_line_pair_routes_one_arm_and_leaves_the_other_untouched",
    ),
    (
        8,
        "chains/test_criteria_validation.py",
        "test_the_loop_receives_the_verdict_and_the_named_resource",
    ),
    (
        8,
        "chains/test_criteria_validation.py",
        "test_unverifiable_only_set_neither_regenerates_nor_halts",
    ),
    (
        8,
        "chains/test_ralph_loop.py",
        "test_the_evaluation_prompt_states_each_criterion_verdict",
    ),
    (
        8,
        "domain/test_criteria_feasibility.py",
        "test_fault_line_pair_routes_one_arm_and_leaves_the_other_untouched",
    ),
    (
        8,
        "domain/test_criteria_feasibility.py",
        "test_unverifiable_only_set_consumes_no_regeneration_round",
    ),
    (9, "domain/test_criteria_feasibility.py", "test_classification_table"),
    (
        10,
        "domain/test_criteria_feasibility.py",
        "test_unmeasured_cost_claim_is_struck_and_recorded",
    ),
    (
        11,
        "domain/test_criteria_feasibility.py",
        "test_a_limit_without_a_measurement_is_never_the_uneconomic_arm",
    ),
    (
        12,
        "domain/test_criteria_feasibility.py",
        "test_repair_set_has_exactly_three_members",
    ),
    (
        13,
        "chains/test_criteria_validation.py",
        "test_no_forbidden_class_or_non_domain_arm_reaches_the_loop",
    ),
    (
        13,
        "chains/test_criteria_validation.py",
        "test_the_pattern_5_fixture_still_names_an_arm_its_type_lacks",
    ),
    (
        14,
        "domain/test_criteria_feasibility.py",
        "test_a_payload_without_the_criterion_class_fails_validation",
    ),
    (
        14,
        "domain/test_criteria_feasibility.py",
        "test_criterion_class_round_trips_under_its_camel_case_alias",
    ),
    (
        14,
        "domain/test_criteria_feasibility.py",
        "test_ids_are_minted_in_emission_order",
    ),
    (
        15,
        "chains/test_accept_gate.py",
        "test_a_flag_contradicting_the_verdict_does_not_move_the_route",
    ),
    (
        15,
        "chains/test_accept_gate.py",
        "test_a_soft_signal_only_failure_reaches_open_pr_with_its_flags",
    ),
    (15, "chains/test_accept_gate.py", "test_all_pass_is_accepted"),
    (
        15,
        "chains/test_accept_gate.py",
        "test_the_arithmetic_cannot_read_a_flag_because_it_is_not_given_one",
    ),
    (
        16,
        "chains/test_accept_gate.py",
        "test_sherlock_flags_round_trip_through_the_camel_case_alias",
    ),
    (
        17,
        "chains/test_accept_gate.py",
        "test_a_flag_the_evaluator_raised_reaches_the_pull_request_body",
    ),
    (
        17,
        "chains/test_accept_gate.py",
        "test_a_soft_signal_only_failure_reaches_open_pr_with_its_flags",
    ),
    (
        18,
        "integration/test_criteria_oracle.py",
        "test_the_oracle_is_byte_identical_across_all_four_surfaces",
    ),
    (
        19,
        "domain/test_criteria_grading.py",
        "test_partial_return_grades_the_missing_ids_failed_and_keeps_the_denominator",
    ),
    (
        20,
        "domain/test_criteria_grading.py",
        "test_echoed_text_mutation_changes_neither_keying_nor_reinjected_text",
    ),
    (
        20,
        "domain/test_criteria_grading.py",
        "test_partial_return_grades_the_missing_ids_failed_and_keeps_the_denominator",
    ),
    (
        20,
        "integration/test_criteria_oracle.py",
        "test_the_second_iteration_is_asked_about_the_harness_text",
    ),
    (
        21,
        "integration/test_stacked_scope.py",
        "test_a_three_level_stack_keeps_the_inherited_lines",
    ),
    (
        22,
        "api/v1/test_jobs.py",
        "test_a_request_with_no_recorded_base_is_a_trunk_fired_lane",
    ),
    (
        22,
        "chains/test_ralph_loop.py",
        "test_the_digest_of_a_stacked_lane_uses_its_recorded_base",
    ),
    (
        22,
        "chains/test_ralph_workflow.py",
        "test_review_of_a_stacked_lane_resolves_its_recorded_base_not_trunk",
    ),
    (22, "domain/test_base_scope.py", "test_a_trunk_fired_lane_computes_against_trunk"),
    (
        22,
        "integration/test_stacked_scope.py",
        "test_a_trunk_fired_ticket_still_computes_against_trunk",
    ),
    (
        22,
        "integration/test_stacked_scope.py",
        "test_the_emitted_event_names_the_ref_that_was_compared",
    ),
    (
        23,
        "chains/test_criteria_validation.py",
        "test_a_scope_criterion_with_no_stated_base_is_regenerated",
    ),
    (
        24,
        "integration/test_stacked_scope.py",
        "test_the_emitted_event_names_the_ref_that_was_compared",
    ),
    (
        25,
        "integration/test_stacked_scope.py",
        "test_a_combined_base_keeps_every_input_intact",
    ),
    (
        26,
        "api/v1/test_jobs.py",
        "test_a_recorded_base_is_dispatched_and_the_trunk_default_is_not",
    ),
    (
        26,
        "chains/test_ralph_loop.py",
        "test_the_digest_of_a_stacked_lane_uses_its_recorded_base",
    ),
    (
        26,
        "chains/test_ralph_loop.py",
        "test_the_first_iteration_is_dispatched_with_the_recorded_base",
    ),
    (
        26,
        "domain/test_base_scope.py",
        "test_no_scope_surface_parses_a_branch_name_to_obtain_a_base",
    ),
    (26, "domain/test_base_scope.py", "test_the_context_holds_no_base_of_its_own"),
    (
        27,
        "chains/test_ralph_workflow.py",
        "test_a_stale_recorded_base_produces_no_scope_verdict_at_all",
    ),
    (27, "domain/test_base_scope.py", "test_a_live_base_produces_a_verdict"),
)


def _markers_in(path: Path) -> set[tuple[int, str, str]]:
    """Every ``(criterion, path, test)`` record one test file carries."""
    lines = path.read_text(encoding="utf-8").splitlines()
    definitions = [
        (number, match.group(1))
        for number, line in enumerate(lines)
        if (match := TEST_DEF.match(line))
    ]
    relative = path.relative_to(TESTS_ROOT).as_posix()
    found: set[tuple[int, str, str]] = set()
    for number, line in enumerate(lines):
        criteria = {int(match.group(1)) for match in CITATION.finditer(line)}
        if not criteria:
            continue
        enclosing = [name for at, name in definitions if at <= number]
        below = [name for at, name in definitions if at >= number]
        if line[:1].isspace() and enclosing:
            anchor = enclosing[-1]
        elif below:
            anchor = below[0]
        elif enclosing:
            anchor = enclosing[-1]
        else:
            msg = f"{relative}:{number + 1} cites a criterion with no test under it"
            raise AssertionError(msg)
        found.update((criterion, relative, anchor) for criterion in criteria)
    return found


def cited_demonstrations() -> set[tuple[int, str, str]]:
    """The map as the tree currently states it.

    The file holding the map is skipped: a marker here would be a record,
    not a demonstration.
    """
    return {
        record
        for path in TESTS_ROOT.rglob("*.py")
        if path.name != MAP_FILE
        for record in _markers_in(path)
    }


def cited_criteria() -> set[int]:
    """Every lane criterion number cited anywhere under ``tests/``."""
    return {criterion for criterion, _, _ in cited_demonstrations()}


def test_every_lane_criterion_carries_a_citation() -> None:
    """A criterion with no cited demonstration is unfindable, not satisfied."""
    expected = set(range(1, LANE_CRITERION_COUNT + 1))
    assert expected - cited_criteria() == set()


def test_no_citation_names_a_criterion_the_lane_does_not_have() -> None:
    """The failure mode R6 calls worse than silence: a marker pointing nowhere."""
    assert {n for n in cited_criteria() if n > LANE_CRITERION_COUNT or n < 1} == set()


def test_every_marker_sits_on_the_demonstration_the_map_records() -> None:
    """The second clause of R6, which counting markers cannot reach.

    Equality both ways: a marker re-pointed at a different live id appears
    as a record the map does not hold, and a demonstration that lost its
    marker appears as a record the tree no longer carries.  Re-point the
    ``AC-22`` marker on ``test_a_trunk_fired_lane_computes_against_trunk``
    at ``AC-4`` and this is the assertion that fails.
    """
    assert cited_demonstrations() == set(DEMONSTRATIONS)
