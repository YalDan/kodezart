"""Native semantic addresses and report arithmetic preserve exact identities.

The session roots below are derived by a static reading of every call in
``src/kodezart``, each name resolved by object in its module's namespace after
import. What that reading does not read, stated once and held by a committed
negative for each shape: a value handed across a function boundary, where the
other function is not resolved at this site (returned from a helper, stored on
an object and read elsewhere, or passed through a container built elsewhere); a
name built at run time; a binding made only when a function runs (`setattr` or
`globals()` inside a function body).
"""

import ast
import builtins
import functools
import importlib
import importlib.util
import inspect
import operator
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, TypeAliasType, TypeVar, get_args, get_origin

import pytest
from pydantic import BaseModel, RootModel, TypeAdapter, ValidationError, create_model

import kodezart
import kodezart.types.domain as types_domain
from kodezart.domain.amendment import (
    NativeWriteRefusalError,
    escalation_question,
    repeated_upheld,
    upheld_reason,
)
from kodezart.handlers.agent_handler import _queued_event_payload
from kodezart.types.domain.agent import NativeAmendmentEvent
from kodezart.types.domain.agent import RulingId as ExistingRulingId
from kodezart.types.domain.amendment import (
    ESCALATED_REASONS,
    AmendedAmendment,
    AmendmentClaim,
    AmendmentGround,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentSubject,
    CriterionSubject,
    NativeWriterOutput,
    RecordedRefusal,
    RulingSubject,
    UpheldAmendment,
    UpheldJudgment,
    UpheldReason,
)
from kodezart.types.domain.amendment_write import AmendmentTextOutput
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.criteria import FindingEvidence
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.ruling_id import RulingId
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


def record(
    kind="criterion", identity="opaque/criterion", reason="ground_not_reproduced"
):
    subject = TypeAdapter(AmendmentSubject).validate_python(
        {"kind": kind, "id": identity}
    )
    finding = {"verdict": "feasible", "smallest_repair": "none"}
    if reason.startswith("cost_measured"):
        finding["cost_claim"] = {
            "assertion": "A measured cost",
            "measurement": {
                "observed": "Executed once at base; 2 seconds observed",
                "affordable": True,
            },
        }
    judgment = AmendmentJudgment(
        subject=subject,
        base_sha="a" * 40,
        ground="unsatisfiable_at_base",
        reproduced=False,
        finding=finding,
        citations=(),
        measured_by="Recorded base experiment"
        if reason.startswith("cost_measured")
        else None,
    )
    claim = AmendmentClaim(
        subject=subject,
        stage="implementation",
        ground="unsatisfiable_at_base",
        departure="A proposed change",
        claimed_capability=None,
    )
    return UpheldAmendment(
        claim=claim,
        reason=reason,
        judgment=judgment,
        publication=RecordedRefusal(
            record=WriteBackResult(
                verdict="holds",
                artifact=TrackerArtifact(
                    surface=WritableSurface(
                        kind=SurfaceKind.MARKER_COMMENT,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=identity),
                        marker="[amendment:fixture]",
                    ),
                    native_ref="actual-record",
                    content="Fixture of an independently verified record.",
                ),
                rounds=(
                    WriteBackFinding(
                        verdict="holds", evidence="Fixture verified.", cited_refs=()
                    ),
                ),
            )
        ),
    )


def _holds(artifact):
    return WriteBackResult(
        verdict="holds",
        artifact=artifact,
        rounds=(
            WriteBackFinding(
                verdict="holds", evidence="Fixture verified.", cited_refs=()
            ),
        ),
    )


def amended(identity="opaque/criterion"):
    """An applied amendment on a criterion subject, the arm that carries no reason."""
    subject = TypeAdapter(AmendmentSubject).validate_python(
        {"kind": "criterion", "id": identity}
    )
    surface = WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=identity),
    )
    prior = TrackerArtifact(
        surface=surface, native_ref=identity, content="[prior criterion row]"
    )
    return AmendedAmendment(
        claim=AmendmentClaim(
            subject=subject,
            stage="implementation",
            ground="unsatisfiable_at_base",
            departure="A proposed change",
            claimed_capability=None,
        ),
        judgment=AmendmentJudgment(
            subject=subject,
            base_sha="a" * 40,
            ground="unsatisfiable_at_base",
            reproduced=True,
            finding={
                "verdict": "infeasible",
                "smallest_repair": "criterion_text",
                "refutation": "No implementation at base satisfies the text.",
            },
            citations=({"path": "policy.py", "quote": "def answer(): return 42"},),
            measured_by=None,
        ),
        prior=prior,
        archive=_holds(
            TrackerArtifact(
                surface=WritableSurface(
                    kind=SurfaceKind.MARKER_COMMENT,
                    ref=surface.ref,
                    marker="[amendment:fixture]",
                ),
                native_ref="actual-archive",
                content="[archived prior criterion row]",
            )
        ),
        applied=_holds(
            TrackerArtifact(
                surface=surface,
                native_ref=identity,
                content="[amended criterion row]",
            )
        ),
    )


def test_same_ruling_newtype_object_reexported_and_native_ids_remain_opaque():
    assert ExistingRulingId is RulingId
    for identity in ["vendor/二", "KOD-97-AC-3", "some key"]:
        assert CriterionSubject(id=identity).id == identity
    for identity in ["", "  ", "\n"]:
        with pytest.raises(ValidationError):
            CriterionSubject(id=identity)


def test_pure_counts_separate_subject_kind_identity_and_reason():
    a = record()
    # The same id string under a different kind: a count that drops the kind
    # merges this with `a`.
    ruling = record(kind="ruling")
    # The same subject as `a` under a second reason, repeated so that one
    # subject carries two distinct counted rows.
    other_reason = record(reason="cost_measured_affordable")
    # A second criterion identity upheld once for the same reason as `a`: a
    # count that drops the id merges it into `a` and reaches four.
    second = record(identity="opaque/criterion-2")
    reports = [
        AmendmentReport(verdicts=items)
        for items in [
            (a, ruling),
            (other_reason,),
            (a, ruling),
            (a, second),
            (other_reason,),
        ]
    ]
    before = [report.model_dump_json() for report in reports]
    counted = repeated_upheld(reports)
    # Read after the first call and before any second one: a second call
    # undoes an in-place mutation that is its own inverse.
    assert [report.model_dump_json() for report in reports] == before
    assert [
        (item.subject.kind, item.subject.id, item.reason, item.count)
        for item in counted
    ] == [
        ("criterion", "opaque/criterion", UpheldReason.COST_MEASURED_AFFORDABLE, 2),
        ("criterion", "opaque/criterion", UpheldReason.GROUND_NOT_REPRODUCED, 3),
        ("ruling", "opaque/criterion", UpheldReason.GROUND_NOT_REPRODUCED, 2),
    ]
    event = NativeAmendmentEvent(report=reports[-1], repeated=counted)
    assert (
        NativeAmendmentEvent.model_validate_json(event.model_dump_json()).repeated
        == counted
    )
    assert repeated_upheld(reports) == counted


def test_the_ground_vocabulary_gained_no_member_when_the_subject_widened():
    """The subject is a discriminated union carrying a typed id, not a fifth ground.

    Widening the subject to the pinned-answer kind added no member here: the same
    four grounds are read against whichever kind the claim names.

    The subject itself stays two members, derived off the annotation rather than
    listed: a change to a test a pinned record designates as protected is
    addressed under that record's existing member, so no third member is owed.
    """
    assert [(member.name, member.value) for member in AmendmentGround] == [
        ("UNSATISFIABLE_AT_BASE", "unsatisfiable_at_base"),
        ("MUTUALLY_UNSATISFIABLE", "mutually_unsatisfiable"),
        ("PREMISE_FALSE_AT_BASE", "premise_false_at_base"),
        ("REQUIRES_BREAKING_HOUSE_RULE", "requires_breaking_house_rule"),
    ]
    members = get_args(get_args(AmendmentSubject)[0])
    assert [member.model_fields["kind"].default for member in members] == [
        "criterion",
        "ruling",
    ]
    with pytest.raises(ValidationError):
        TypeAdapter(AmendmentSubject).validate_python(
            {"kind": "protected_test", "id": "tests/test_pinned_boundary.py"}
        )
    value = amended()
    with pytest.raises(ValidationError):
        AmendmentClaim.model_validate(
            value.claim.model_dump() | {"ground": "a_fifth_ground"}
        )
    with pytest.raises(ValidationError):
        AmendmentJudgment.model_validate(
            value.judgment.model_dump() | {"ground": "a_fifth_ground"}
        )


def test_the_reason_vocabulary_is_exactly_these_four():
    assert [(member.name, member.value) for member in UpheldReason] == [
        ("GROUND_NOT_REPRODUCED", "ground_not_reproduced"),
        ("ENVIRONMENT_LACKS_CAPABILITY", "environment_lacks_capability"),
        ("COST_MEASURED_AFFORDABLE", "cost_measured_affordable"),
        ("COST_MEASURED_UNECONOMIC", "cost_measured_uneconomic"),
    ]


def test_the_escalating_reasons_are_the_two_a_person_must_settle():
    """One statement of which reasons escalate, read by the rule and the routing.

    The other two reasons are statements about the criterion's own text, which
    the next iteration answers by working on it; these two are outside the
    branch's reach, so a person is asked.
    """
    assert ESCALATED_REASONS == {
        UpheldReason.COST_MEASURED_UNECONOMIC,
        UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
    }


#: The question the landed uneconomic escalation already carries, byte for byte.
_UNECONOMIC_QUESTION = "Resolve the measured uneconomic departure for opaque/criterion"

#: What the fixture's demonstration lacks, worded so it names no capability: the
#: capability in a composed question can only have come from the typed claim.
_MISSING_RESOURCE = "an outbound connection to the package index"

#: A pinned subject's identity, worded so it names no criterion.
_PINNED_ID = "pinned/offline-mirror"


@pytest.mark.parametrize(
    "reason,claimed,subject,expected",
    [
        pytest.param(
            UpheldReason.COST_MEASURED_UNECONOMIC,
            None,
            None,
            _UNECONOMIC_QUESTION,
            id="uneconomic_keeps_its_own_question",
        ),
        pytest.param(
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            CheckPrerequisite.NETWORK,
            None,
            (
                "Resolve the missing capability network for opaque/criterion: the "
                "demonstration needs an outbound connection to the package index, "
                "which the declared runner environment does not provide. Declaring "
                "network in the repository's runner environment and removing the "
                "decision classification from this issue, then firing again, revives "
                "it; otherwise a person cancels the criterion with a supersession."
            ),
            id="capability_names_the_revival_condition",
        ),
        pytest.param(
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            CheckPrerequisite.CREDENTIALS,
            None,
            (
                "Resolve the missing capability credentials for opaque/criterion: the "
                "demonstration needs an outbound connection to the package index, "
                "which the declared runner environment does not provide. Declaring "
                "credentials in the repository's runner environment and removing the "
                "decision classification from this issue, then firing again, revives "
                "it; otherwise a person cancels the criterion with a supersession."
            ),
            id="capability_is_interpolated_from_the_claim",
        ),
        pytest.param(
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            CheckPrerequisite.NETWORK,
            RulingSubject(id=_PINNED_ID),
            (
                "Resolve the missing capability network for pinned/offline-mirror: the"
                " demonstration needs an outbound connection to the package index, "
                "which the declared runner environment does not provide. Declaring "
                "network in the repository's runner environment and removing the "
                "decision classification from this issue, then firing again, revives "
                "it."
            ),
            id="a_pinned_subject_is_named_as_itself",
        ),
        pytest.param(
            UpheldReason.GROUND_NOT_REPRODUCED,
            None,
            None,
            NativeWriteRefusalError,
            id="ground_raises_nothing",
        ),
        pytest.param(
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            None,
            None,
            NativeWriteRefusalError,
            id="capability_without_a_typed_claim_refuses",
        ),
    ],
)
def test_the_escalation_question_names_the_capability_and_the_revival_condition(
    reason, claimed, subject, expected
):
    """Every reason is answered, and the two that raise nothing refuse as types.

    The uneconomic arm's question is the literal it already was, so admitting the
    second reason moves no existing escalation's content. The capability arm names
    what is absent, what the demonstration needs, and what would revive it: the
    capability declared and the `decision` classification the escalation adds
    removed, since the plan read refuses while that classification stands. A
    criterion is also offered the cancellation left to a person; a pinned subject
    is named as itself and offered nothing a criterion alone has. A reason that
    raises no escalation, and a missing-capability reason with no typed claim to
    name, refuse as types rather than composing an empty question.
    """
    value = record()
    claim = AmendmentClaim.model_validate(
        value.claim.model_dump()
        | {"claimed_capability": claimed}
        | ({} if subject is None else {"subject": subject})
    )
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump()
        | {
            "finding": {
                "verdict": "unverifiable",
                "smallest_repair": "environment_supply",
                "missing_resource": _MISSING_RESOURCE,
            }
        }
    )
    if expected is NativeWriteRefusalError:
        with pytest.raises(NativeWriteRefusalError):
            escalation_question(reason=reason, claim=claim, judgment=judgment)
        return
    question = escalation_question(reason=reason, claim=claim, judgment=judgment)
    assert question == expected
    if subject is not None:
        assert _PINNED_ID in question
        assert "criterion" not in question


def test_a_verdict_and_its_reason_cannot_be_constructed_apart():
    """The pairing is the shape: upheld requires a reason, amended has no such field.

    No validator states it — an upheld record carries `reason` as a required,
    non-nullable field and an amended record declares none under `extra="forbid"`,
    so the bad pairing is unconstructible rather than rejected.
    """
    upheld = record().model_dump()
    assert (
        UpheldAmendment.model_validate(upheld).reason
        is UpheldReason.GROUND_NOT_REPRODUCED
    )
    for broken in (
        {key: value for key, value in upheld.items() if key != "reason"},
        upheld | {"reason": None},
    ):
        with pytest.raises(ValidationError):
            UpheldAmendment.model_validate(broken)
        with pytest.raises(ValidationError):
            AmendmentReport.model_validate({"verdicts": [broken]})
    applied = amended().model_dump()
    assert (
        AmendmentReport.model_validate({"verdicts": [applied]}).verdicts[0].verdict
        == "amended"
    )
    for reason in (*[member.value for member in UpheldReason], None):
        with pytest.raises(ValidationError):
            AmendedAmendment.model_validate(applied | {"reason": reason})
        with pytest.raises(ValidationError):
            AmendmentReport.model_validate({"verdicts": [applied | {"reason": reason}]})


#: The fault lies outside the criterion: some implementation at base would
#: satisfy it, and only the demonstration is unavailable in this environment.
_FAULT_OUTSIDE = {
    "verdict": "unverifiable",
    "smallest_repair": "environment_supply",
    "missing_resource": "network access for the demonstration",
}

#: A measured cost the measurement shows is incurred, before either half of how
#: it was produced is taken away by a row.
_UNECONOMIC_COST = {
    "assertion": "The demonstration costs too much to run.",
    "measurement": {
        "observed": "Executed once at base; 9 hours observed",
        "affordable": False,
    },
}


@pytest.mark.parametrize(
    "judgment_changes,claimed,environment,expected",
    [
        pytest.param({}, None, {}, None, id="fault_in_criterion"),
        pytest.param(
            {},
            "network",
            {CheckPrerequisite.NETWORK: False},
            None,
            id="fault_in_criterion_with_capability_claimed",
        ),
        pytest.param(
            {"finding": _FAULT_OUTSIDE},
            None,
            {},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="fault_outside_criterion",
        ),
        pytest.param(
            {"finding": _FAULT_OUTSIDE},
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            id="fault_outside_criterion_declared_absent",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {
                    "cost_claim": {
                        "assertion": "The demonstration costs what it costs.",
                        "measurement": {
                            "observed": "Executed once at base; 2 seconds observed",
                            "affordable": True,
                        },
                    }
                },
                "measured_by": "Reproduced recorded command at base",
            },
            None,
            {},
            UpheldReason.COST_MEASURED_AFFORDABLE,
            id="cost_decides_before_the_fault_line",
        ),
        pytest.param(
            {"finding": _FAULT_OUTSIDE, "reproduced": False},
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            id="fault_outside_criterion_asked_before_reproduction",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {
                    "cost_claim": {
                        "assertion": "The demonstration costs too much to run.",
                        "measurement": {
                            "observed": "Executed once at base; 9 hours observed",
                            "affordable": False,
                        },
                    }
                },
                "measured_by": "Reproduced recorded command at base",
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.COST_MEASURED_UNECONOMIC,
            id="cost_uneconomic_decides_before_the_fault_line",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {
                    "cost_claim": {
                        "assertion": "The demonstration costs what it costs.",
                        "measurement": {
                            "observed": "Executed once at base; 2 seconds observed",
                            "affordable": True,
                        },
                    }
                },
                "measured_by": "Reproduced recorded command at base",
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.COST_MEASURED_AFFORDABLE,
            id="cost_affordable_with_capability_claimed",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {"cost_claim": {"assertion": "The demonstration is expensive."}},
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="cost_unmeasured_is_not_undemonstrability",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE | {"cost_claim": _UNECONOMIC_COST},
                "measured_by": None,
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="cost_measured_with_no_instrument_is_not_undemonstrability",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {"cost_claim": _UNECONOMIC_COST | {"measurement": None}},
                "measured_by": "Reproduced recorded command at base",
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="cost_instrument_with_no_measurement_is_not_undemonstrability",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE | {"cost_claim": _UNECONOMIC_COST},
                "measured_by": "Reproduced recorded command at base",
                "citations": [],
            },
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="cost_measured_uncited_is_not_undemonstrability",
        ),
    ],
)
def test_the_fault_line_is_asked_after_cost_and_before_reproduction(
    judgment_changes,
    claimed,
    environment,
    expected,
):
    """Would some implementation at base satisfy this criterion, asked first.

    The rows differ in the finding alone where the fault line is what decides:
    a fault in the criterion's own text authorizes an amendment, a fault outside
    it never does. Cost is not one of the four grounds, so deciding it above the
    fault line still asks the fault line before any ground, and that landed
    order is pinned here, the reproduction half by the unreproduced row.

    The cost rows each claim a capability the declared environment lacks, so
    only the cost can decide them: a departure resting on a cost claim returns
    its own measured reason, or the ground when nothing was measured, and never
    the environment reason, which the capability rows beside them reach. A
    measurement counts only whole: one with no instrument, an instrument with no
    measurement, or a measurement cited by nothing each resolve to the ground.
    """
    value = amended()
    claim = AmendmentClaim.model_validate(
        value.claim.model_dump() | {"claimed_capability": claimed}
    )
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump() | judgment_changes
    )
    assert judgment.reproduced is judgment_changes.get("reproduced", True)
    assert upheld_reason(claim, judgment, environment=environment) is expected


def test_claim_cannot_carry_writer_reasoning_unknown_stage_or_unknown_ground():
    valid = {
        "subject": {"kind": "criterion", "id": "native/1"},
        "stage": "implementation",
        "ground": "premise_false_at_base",
        "departure": "Different behavior",
        "claimed_capability": None,
    }
    assert NativeWriterOutput(claims=[AmendmentClaim(**valid)])
    for changes in [
        {"reasoning": "trust me"},
        {"stage": "guessed_fix"},
        {"ground": "a_quote_exists"},
        {"claimed_capability": "unknown"},
    ]:
        with pytest.raises(ValidationError):
            AmendmentClaim.model_validate(valid | changes)
    with pytest.raises(ValidationError):
        NativeWriterOutput(claims=[valid, valid])


def _annotation_types(annotation, followed=()):
    """Every type mentioned anywhere inside one field's annotation.

    Follows the arguments of a subscripted annotation, the value a PEP 695
    alias (``type X = ...``) stands for, and a type variable's bound and
    constraints, none of which ``get_args`` reaches. *followed* holds the
    aliases and type variables already being followed on this path, so a
    recursive alias ends the walk instead of looping.
    """
    yield annotation
    if isinstance(annotation, TypeAliasType | TypeVar):
        if annotation in followed:
            return
        followed = (*followed, annotation)
    if isinstance(annotation, TypeAliasType):
        yield from _annotation_types(annotation.__value__, followed)
    if isinstance(annotation, TypeVar):
        for bound in (annotation.__bound__, *annotation.__constraints__):
            if bound is not None:
                yield from _annotation_types(bound, followed)
    for argument in get_args(annotation):
        yield from _annotation_types(argument, followed)


#: The parameters through which a session is handed the schema of its output:
#: a judged session's ``output_schema`` and an executor's ``output_format``,
#: whose ``schema`` entry is that schema.
SCHEMA_PARAMETERS = frozenset({"output_schema", "output_format"})

#: How many bindings deep one argument is followed before the walk gives up, so
#: a binding that names itself, directly or around a cycle, ends the walk.
RESOLUTION_DEPTH = 8

_MISSING = object()


@dataclass(frozen=True)
class _Instance:
    """An instance of *cls* built at the call site, known by its class alone."""

    cls: type


@dataclass(frozen=True)
class _Bound:
    """A function bound to an instance, so its first parameter is already filled."""

    function: Callable[..., object]


@dataclass(frozen=True)
class _Scope:
    """What one call site's names denote: its functions' bindings, then the module."""

    #: The module's own namespace after import.
    namespace: Mapping[str, object]
    #: The local bindings of each enclosing function, innermost first.
    bindings: tuple[Mapping[str, tuple[ast.expr, ...]], ...] = ()


def _local_bindings(function):
    """Every value a function body binds to a plain name, walrus bindings included."""
    found: dict[str, list[ast.expr]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value:
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.setdefault(target.id, []).append(node.value)
    return {name: tuple(values) for name, values in found.items()}


def _is_schema_method(value):
    """Whether *value* is ``model_json_schema`` bound to one model class."""
    return (
        inspect.ismethod(value)
        and isinstance(value.__self__, type)
        and issubclass(value.__self__, BaseModel)
        and value.__func__ is BaseModel.model_json_schema.__func__
    )


def _attribute(receiver, name):
    """What ``receiver.name`` denotes, read without running a descriptor."""
    if isinstance(receiver, _Instance):
        value = inspect.getattr_static(receiver.cls, name, _MISSING)
        return [_Bound(value)] if inspect.isfunction(value) else []
    if name == "__dict__" and isinstance(receiver, ModuleType | type):
        return [vars(receiver)]
    value = inspect.getattr_static(receiver, name, _MISSING)
    if value is _MISSING:
        return []
    if isinstance(value, classmethod) and isinstance(receiver, type):
        return [value.__get__(None, receiver)]
    return [value]


def _call_values(node, scope, depth):
    """What a call evaluates to, for the calls that only look a name up.

    ``getattr``, ``vars``, ``importlib.import_module``, ``__import__`` and
    ``operator.attrgetter`` with literal names; a model's own
    ``model_json_schema``; and a class, whose call is an instance of it.
    """
    literals = [
        [value for value in _values(argument, scope, depth) if isinstance(value, str)]
        for argument in node.args
    ]
    receivers = _values(node.args[0], scope, depth) if node.args else []
    found: list[object] = []
    for callee in _values(node.func, scope, depth):
        if callee is getattr and len(literals) >= 2:
            found += [
                value
                for receiver in receivers
                for name in literals[1]
                for value in _attribute(receiver, name)
            ]
        elif callee is vars:
            found += [
                vars(receiver)
                for receiver in receivers
                if isinstance(receiver, ModuleType | type)
            ]
        elif callee is importlib.import_module and literals:
            found += [importlib.import_module(name) for name in literals[0]]
        elif callee is __import__ and literals:
            for name in literals[0]:
                importlib.import_module(name)
                found.append(importlib.import_module(name.partition(".")[0]))
        elif callee is operator.attrgetter and literals:
            found += [operator.attrgetter(name) for name in literals[0]]
        elif isinstance(callee, operator.attrgetter):
            found += [
                callee(receiver)
                for receiver in receivers
                if isinstance(receiver, ModuleType | type)
            ]
        elif _is_schema_method(callee):
            found.append(callee())
        elif isinstance(callee, type):
            found.append(_Instance(callee))
    return found


def _values(node, scope, depth=0):
    """Every object an expression can evaluate to, read from the code it names.

    A name is looked up in the enclosing functions' bindings, innermost first,
    then in the module's namespace after import, then among the builtins. An
    attribute, a subscript with a literal key, a dict display's values, both
    arms of a conditional, a walrus and a call that only looks a name up are
    followed from there, at most ``RESOLUTION_DEPTH`` deep. Anything else
    evaluates to nothing.
    """
    if depth > RESOLUTION_DEPTH:
        return []
    depth += 1
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, ast.Name):
        for bindings in scope.bindings:
            if node.id in bindings:
                return [
                    value
                    for bound in bindings[node.id]
                    for value in _values(bound, scope, depth)
                ]
        for namespace in (scope.namespace, vars(builtins)):
            if node.id in namespace:
                return [namespace[node.id]]
        return []
    if isinstance(node, ast.Attribute):
        return [
            value
            for receiver in _values(node.value, scope, depth)
            for value in _attribute(receiver, node.attr)
        ]
    if isinstance(node, ast.Subscript):
        return [
            container[key]
            for container in _values(node.value, scope, depth)
            if isinstance(container, Mapping)
            for key in _values(node.slice, scope, depth)
            if isinstance(key, str) and key in container
        ]
    if isinstance(node, ast.Dict):
        return [
            value for entry in node.values for value in _values(entry, scope, depth)
        ]
    if isinstance(node, ast.IfExp):
        return _values(node.body, scope, depth) + _values(node.orelse, scope, depth)
    if isinstance(node, ast.NamedExpr):
        return _values(node.value, scope, depth)
    if isinstance(node, ast.Call):
        return _call_values(node, scope, depth)
    return []


def _positional(node, scope):
    """Each positional argument of a call, paired with the parameter it lands on.

    The callee is resolved by object: a function, a method read off its class
    and called with an explicit instance, a method bound to an instance built
    at the call site or held in a local, or the callee a ``functools.partial``
    wraps, whose own positional arguments come first. A starred list or tuple
    display is spread in place.
    """
    arguments: list[ast.expr] = []
    for argument in node.args:
        if isinstance(argument, ast.Starred) and isinstance(
            argument.value, ast.List | ast.Tuple
        ):
            arguments += argument.value.elts
        else:
            arguments.append(argument)
    for callee in _values(node.func, scope):
        targets, handed = [callee], arguments
        if callee is functools.partial and arguments:
            targets, handed = _values(arguments[0], scope), arguments[1:]
        for target in targets:
            function = target.function if isinstance(target, _Bound) else target
            if not inspect.isfunction(function):
                continue
            parameters = [
                parameter.name
                for parameter in inspect.signature(function).parameters.values()
                if parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            ][isinstance(target, _Bound) :]
            yield from zip(parameters, handed, strict=False)


def _lambda_defaults(node):
    """Each parameter of a lambda that has a default, with that default."""
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    yield from zip(
        positional[len(positional) - len(arguments.defaults) :],
        arguments.defaults,
        strict=True,
    )
    for parameter, default in zip(
        arguments.kwonlyargs, arguments.kw_defaults, strict=True
    ):
        if default is not None:
            yield parameter, default


def _handed_arguments(node, scope):
    """Every expression a call or a lambda hands a session as its output schema.

    A keyword argument, an entry of a ``**`` dict display with a literal key, a
    positional argument that lands on the parameter, and a lambda's default.
    """
    if isinstance(node, ast.Lambda):
        for parameter, default in _lambda_defaults(node):
            if parameter.arg in SCHEMA_PARAMETERS:
                yield default
        return
    for keyword in node.keywords:
        if keyword.arg in SCHEMA_PARAMETERS:
            yield keyword.value
        elif keyword.arg is None and isinstance(keyword.value, ast.Dict):
            for key, value in zip(
                keyword.value.keys, keyword.value.values, strict=True
            ):
                if isinstance(key, ast.Constant) and key.value in SCHEMA_PARAMETERS:
                    yield value
    for parameter, argument in _positional(node, scope):
        if parameter in SCHEMA_PARAMETERS:
            yield argument


def handed_schemas(tree, namespace):
    """Every object one module hands a session as the schema of its output.

    Every call and lambda in *tree* is read in its own scope: the bindings of
    the functions around it, then *namespace*, the module's namespace after
    import. The walk visits each node of the tree once.
    """
    found: list[object] = []

    def visit(node, bindings):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            bindings = (_local_bindings(node), *bindings)
        if isinstance(node, ast.Call | ast.Lambda):
            scope = _Scope(namespace, bindings)
            for argument in _handed_arguments(node, scope):
                found.extend(_values(argument, scope))
        for child in ast.iter_child_nodes(node):
            visit(child, bindings)

    visit(tree, ())
    return found


def schema_models(schemas, models):
    """The models of *models* whose schema is one of *schemas*, and the rest.

    Returns every matching model, then every schema no model produces. Only a
    dict is a schema; any other object handed over is not counted.
    """
    produced = [(model, model.model_json_schema()) for model in models]
    matched: list[type[BaseModel]] = []
    unmatched: list[object] = []
    for handed in schemas:
        if not isinstance(handed, dict):
            continue
        found = [model for model, schema in produced if schema == handed]
        matched += found
        if not found:
            unmatched.append(handed)
    return matched, unmatched


@functools.cache
def session_roots():
    """Every model whose schema ``src/kodezart`` hands a session for its output.

    Derived by reading every call in every module of the installed package,
    resolved against that module after import, and matching each schema handed
    over to the model that produces it. Returns the roots and every schema that
    no model produces.
    """
    modules = [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(kodezart.__path__, f"{kodezart.__name__}.")
    ]
    models = [
        value
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == module.__name__
    ]
    schemas = [
        schema
        for module in modules
        for schema in handed_schemas(
            ast.parse(Path(module.__file__).read_text(encoding="utf-8")), vars(module)
        )
    ]
    roots, unmatched = schema_models(schemas, models)
    return tuple(
        sorted(set(roots), key=lambda model: (model.__module__, model.__qualname__))
    ), unmatched


#: The verdicts a session's judgment is judged by. The code builds them from a
#: validated judgment and hands no session their schema, so the derivation does
#: not find them; they are held here so the snapshot still pins them and the
#: refusal publications they carry.
JUDGED_BY = (UpheldJudgment, UpheldAmendment)


def _reachable_models(roots):
    """Every model reachable from *roots* through field annotations, roots included.

    Recurses into the models a field names as well as into the arguments of
    its annotation, so a model nested any depth below a root is scanned.
    """
    seen: list[type[BaseModel]] = []
    pending = list(roots)
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.append(model)
        for field in model.model_fields.values():
            for found in _annotation_types(field.annotation):
                if isinstance(found, type) and issubclass(found, BaseModel):
                    pending.append(found)
    return seen


def _is_truth_valued_mapping(annotation):
    """A mapping whose values are truth values, whatever its keys."""
    origin = get_origin(annotation)
    if not (isinstance(origin, type) and issubclass(origin, Mapping)):
        return False
    arguments = get_args(annotation)
    return len(arguments) == 2 and any(
        found is bool for found in _annotation_types(arguments[1])
    )


def _declared_models():
    """Every model declared under `kodezart.types.domain`, found by walking it.

    Derived rather than listed, so a model added to the package is scanned
    without anyone remembering to add it here.
    """
    for info in pkgutil.iter_modules(types_domain.__path__):
        module = importlib.import_module(f"{types_domain.__name__}.{info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                yield value


#: Every field of every model in the session-facing closure, with its declared
#: type as ``repr`` renders it. Taken from the closure at this commit; a model or
#: field added, removed or retyped anywhere in the closure changes it.
SESSION_CLOSURE = {
    "AcceptanceCriteriaOutput": {
        "criteria_results": "list[kodezart.types.domain.agent.CriterionResult]",
        "sherlock_flags": "list[kodezart.types.domain.accept.SherlockFlag]",
    },
    "AdmissionJudgment": {
        "root": (
            "typing.Annotated[kodezart.types.domain.organize.BuildableAdmission | "
            "kodezart.types.domain.organize.RefusedAdmission | "
            "kodezart.types.domain.organize.UnverifiableAdmission, "
            "FieldInfo(annotation=NoneType, required=True, discriminator='verdict')]"
        ),
    },
    "AmendmentClaim": {
        "subject": (
            "kodezart.types.domain.amendment.CriterionSubject | "
            "kodezart.types.domain.amendment.RulingSubject"
        ),
        "stage": "typing.Literal['implementation']",
        "ground": "<enum 'AmendmentGround'>",
        "departure": "<class 'str'>",
        "claimed_capability": (
            "kodezart.types.domain.operation.CheckPrerequisite | None"
        ),
    },
    "AmendmentJudgment": {
        "subject": (
            "kodezart.types.domain.amendment.CriterionSubject | "
            "kodezart.types.domain.amendment.RulingSubject"
        ),
        "base_sha": "<class 'str'>",
        "ground": "<enum 'AmendmentGround'>",
        "reproduced": "<class 'bool'>",
        "finding": "<class 'kodezart.types.domain.criteria.FindingEvidence'>",
        "citations": "tuple[kodezart.types.domain.amendment.BaseCitation, ...]",
        "measured_by": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
    },
    "AmendmentTextOutput": {
        "replacement": (
            "kodezart.types.domain.amendment_write.CriterionReplacement | "
            "kodezart.types.domain.amendment_write.RulingReplacement | "
            "kodezart.types.domain.amendment_write.PreservedSubject"
        ),
        "explanation": "<class 'str'>",
    },
    "AuditBytePair": {
        "source_sha": "<class 'str'>",
        "source_path": "<class 'str'>",
        "artifact_path": "<class 'str'>",
    },
    "AuditClaimJudgment": {
        "criterion_key": "<class 'str'>",
        "verdict": "+ClaimVerdict",
        "evidence": "<class 'str'>",
    },
    "AuditMandateJudgment": {
        "root": "MandateJudgment",
    },
    "AuditOverclaimJudgment": {
        "criterion_key": "<class 'str'>",
        "checks": "tuple[kodezart.types.domain.audit_overclaim.OverclaimReading, ...]",
        "byte_pairs": "tuple[kodezart.types.domain.audit_overclaim.AuditBytePair, ...]",
    },
    "BaseCitation": {
        "path": "<class 'str'>",
        "quote": "<class 'str'>",
    },
    "BaseDemonstration": {
        "command": "<class 'str'>",
        "satisfied_at_base": "<class 'bool'>",
    },
    "BlockedByChange": {
        "add": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
        "remove": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
        "kind": "typing.Literal['blocked_by']",
    },
    "BodyProposal": {
        "kind": "typing.Literal['body']",
        "issue_id": "<class 'str'>",
        "body": "<class 'str'>",
    },
    "BranchNameOutput": {
        "slug": "<class 'str'>",
    },
    "BuildableAdmission": {
        "verdict": "typing.Literal[<AdmissionVerdict.BUILDABLE: 'buildable'>]",
        "issue_id": "<class 'str'>",
        "evidence": "<class 'str'>",
        "findings": "tuple[kodezart.types.domain.organize.SpecFinding, ...]",
    },
    "CodeReference": {
        "location": "<class 'str'>",
        "note": "<class 'str'>",
    },
    "CommitMessageOutput": {
        "title": "<class 'str'>",
        "body": "<class 'str'>",
    },
    "ContentAuditFinding": {
        "category": (
            "typing.Union[typing.Literal[<RedactionCategory.ORG_PRIVATE: "
            "'org_private'>], kodezart.types.domain.gating.DurabilityCategory]"
        ),
        "start": "int | None",
        "end": "int | None",
        "rationale": "<class 'str'>",
    },
    "ContentAuditOutput": {
        "findings": "list[kodezart.types.domain.agent.ContentAuditFinding]",
    },
    "Contradiction": {
        "criterion_ids": (
            "list[typing.Annotated[kodezart.types.domain.criteria.CriterionId, "
            "FieldInfo(annotation=NoneType, required=True, metadata=["
            "_PydanticGeneralMetadata(pattern='^AC-[1-9][0-9]*$')])]]"
        ),
        "explanation": "<class 'str'>",
    },
    "CostClaim": {
        "assertion": "<class 'str'>",
        "measurement": "kodezart.types.domain.criteria.CostMeasurement | None",
    },
    "CostMeasurement": {
        "observed": "<class 'str'>",
        "affordable": "<class 'bool'>",
    },
    "CriteriaProposal": {
        "kind": "typing.Literal['criteria']",
        "issue_id": "<class 'str'>",
        "criteria": (
            "tuple[kodezart.types.domain.organize_owner.CriterionProposal, ...]"
        ),
    },
    "CriteriaValidationOutput": {
        "findings": "list[kodezart.types.domain.criteria.CriterionFinding]",
        "contradictions": "list[kodezart.types.domain.criteria.Contradiction]",
    },
    "CriterionFinding": {
        "criterion_id": "kodezart.types.domain.criteria.CriterionId",
        "verdict": "<enum 'CriterionVerdict'>",
        "smallest_repair": "<enum 'RepairKind'>",
        "refutation": "str | None",
        "missing_resource": "str | None",
        "cost_claim": "kodezart.types.domain.criteria.CostClaim | None",
        "base_demonstration": "kodezart.types.domain.criteria.BaseDemonstration | None",
        "pinned_literals": "list[str]",
        "forbidden_class": (
            "kodezart.types.domain.criteria.ForbiddenCriterionClass | None"
        ),
        "undeclared_switch_arms": "list[str]",
    },
    "CriterionProposal": {
        "title": "<class 'str'>",
        "check": "<class 'str'>",
        "do": "<class 'str'>",
    },
    "CriterionReplacement": {
        "kind": "typing.Literal['criterion']",
        "subject": "<class 'kodezart.types.domain.amendment.CriterionSubject'>",
        "check": "<class 'str'>",
        "do": "<class 'str'>",
    },
    "CriterionResult": {
        "criterion_id": "kodezart.types.domain.criteria.CriterionId",
        "criterion": "<class 'str'>",
        "passed": "<class 'bool'>",
        "reasoning": "<class 'str'>",
        "rederivation_class": "<enum 'RederivationClass'>",
        "exercised_paths": "tuple[str, ...]",
    },
    "CriterionSubject": {
        "kind": "typing.Literal['criterion']",
        "id": "kodezart.types.domain.criteria.CriterionId",
    },
    "CritiqueFlag": {
        "subject": "<class 'str'>",
        "reason": "<class 'str'>",
    },
    "DeletedDetectionFinding": {
        "mechanism": (
            "<class 'kodezart.types.domain.audit_detection_removal.RemovedSourceQuote'>"
        ),
        "detector": (
            "<class 'kodezart.types.domain.audit_detection_removal.RemovedSourceQuote'>"
        ),
        "absence_demonstration": "<class 'str'>",
    },
    "DetectorRemovalJudgment": {
        "criterion_key": "kodezart.types.domain.criterion_ref.CriterionRef",
        "verdict": "<enum 'AuditVerdict'>",
        "evidence": "<class 'str'>",
        "findings": (
            "tuple[kodezart.types.domain.audit_detection_removal.DeletedDetectionFind"
            "ing, ...]"
        ),
    },
    "DraftedCriterion": {
        "text": "<class 'str'>",
    },
    "EscalatedRefusal": {
        "kind": "typing.Literal['escalated']",
        "record": "<class 'kodezart.types.domain.write_back.WriteBackResult'>",
        "escalation": "<class 'kodezart.types.domain.write_back.WriteBackResult'>",
    },
    "FileChange": {
        "file_path": "<class 'str'>",
        "change_type": "typing.Literal['create', 'modify', 'delete']",
        "description": "<class 'str'>",
        "rationale": "<class 'str'>",
    },
    "FindingEvidence": {
        "verdict": "<enum 'CriterionVerdict'>",
        "smallest_repair": "<enum 'RepairKind'>",
        "refutation": "str | None",
        "missing_resource": "str | None",
        "cost_claim": "kodezart.types.domain.criteria.CostClaim | None",
        "base_demonstration": "kodezart.types.domain.criteria.BaseDemonstration | None",
        "pinned_literals": "list[str]",
        "forbidden_class": (
            "kodezart.types.domain.criteria.ForbiddenCriterionClass | None"
        ),
        "undeclared_switch_arms": "list[str]",
    },
    "GeneratedCriteriaOutput": {
        "criteria": "list[kodezart.types.domain.criteria.DraftedCriterion]",
        "reasoning": "<class 'str'>",
    },
    "GraphProposal": {
        "kind": "typing.Literal['graph']",
        "issue_id": "<class 'str'>",
        "changes": (
            "tuple[typing.Annotated[kodezart.types.domain.organize_graph.ParentChange"
            " | kodezart.types.domain.organize_graph.BlockedByChange | "
            "kodezart.types.domain.organize_graph.RelatedToChange | "
            "kodezart.types.domain.organize_graph.PriorityChange | "
            "kodezart.types.domain.organize_graph.MilestoneChange, "
            "FieldInfo(annotation=NoneType, required=True, discriminator='kind')], "
            "...]"
        ),
    },
    "MandateAbsent": {
        "evidence": "<class 'str'>",
        "verdict": "typing.Literal[<AuditVerdict.REFUTED: 'refuted'>]",
        "finding": "<class 'NoneType'>",
        "source_index": "<class 'NoneType'>",
    },
    "MandateFinding": {
        "issue_id": "<class 'str'>",
        "defect_class": "<class 'str'>",
        "evidence": "<class 'str'>",
        "role": "typing.Literal[<DefectRole.MANDATE: 'mandate'>]",
        "mandate_text": "<class 'str'>",
    },
    "MandateInstructed": {
        "evidence": "<class 'str'>",
        "verdict": "typing.Literal[<AuditVerdict.HOLDS: 'holds'>]",
        "finding": "<class 'kodezart.types.domain.audit.MandateFinding'>",
        "source_index": "<class 'int'>",
    },
    "MandateUnverifiable": {
        "evidence": "<class 'str'>",
        "verdict": "typing.Literal[<AuditVerdict.UNVERIFIABLE: 'unverifiable'>]",
        "finding": "<class 'NoneType'>",
        "source_index": "<class 'int'>",
    },
    "MilestoneChange": {
        "kind": "typing.Literal['milestone']",
        "milestone_id": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
    },
    "NativeWriterOutput": {
        "claims": "tuple[kodezart.types.domain.amendment.AmendmentClaim, ...]",
    },
    "OrganizeProposal": {
        "root": (
            "typing.Annotated[kodezart.types.domain.organize_owner.BodyProposal | "
            "kodezart.types.domain.organize_owner.CriteriaProposal | "
            "kodezart.types.domain.organize_graph.GraphProposal | "
            "kodezart.types.domain.organize_graph.SplitProposal | "
            "kodezart.types.domain.organize_owner.UnresolvedProposal | "
            "kodezart.types.domain.organize_owner.UnavailableProposal, "
            "FieldInfo(annotation=NoneType, required=True, discriminator='kind')]"
        ),
    },
    "OverclaimReading": {
        "kind": "<enum 'OverclaimKind'>",
        "verdict": "<enum 'AuditVerdict'>",
        "evidence": "<class 'str'>",
        "recomputed_value": "str | None",
        "missing_artifact": "str | None",
    },
    "PRDescriptionOutput": {
        "title": "<class 'str'>",
        "description": "<class 'str'>",
    },
    "ParentChange": {
        "kind": "typing.Literal['parent']",
        "parent_id": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
    },
    "PreservedSubject": {
        "kind": "typing.Literal['preserved']",
    },
    "PriorityChange": {
        "kind": "typing.Literal['priority']",
        "priority": "<enum 'IssuePriority'>",
    },
    "RecordedRefusal": {
        "kind": "typing.Literal['recorded']",
        "record": "<class 'kodezart.types.domain.write_back.WriteBackResult'>",
    },
    "RefusedAdmission": {
        "verdict": "typing.Literal[<AdmissionVerdict.NOT_BUILDABLE: 'not_buildable'>]",
        "issue_id": "<class 'str'>",
        "evidence": "<class 'str'>",
        "findings": "tuple[kodezart.types.domain.organize.SpecFinding, ...]",
        "invented_decision": "<class 'str'>",
        "refusal_kind": "<enum 'RefusalKind'>",
    },
    "RelatedToChange": {
        "add": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
        "remove": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
        "kind": "typing.Literal['related_to']",
    },
    "RemediationPlan": {
        "instructions": "<class 'str'>",
    },
    "RemovedSourceQuote": {
        "path": "<class 'str'>",
        "line": "<class 'int'>",
        "text": "<class 'str'>",
    },
    "RulingAnswer": {
        "issue_ref": "<class 'str'>",
        "question": "<class 'str'>",
        "ruling_class": "<enum 'RulingClass'>",
        "resolution": "<class 'str'>",
        "rejected_alternative": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
        "repo_evidence": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
        "supersedes_question": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
        "deliverable": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
    },
    "RulingOutput": {
        "rulings": "tuple[kodezart.types.domain.agent.RulingAnswer, ...]",
    },
    "RulingReplacement": {
        "kind": "typing.Literal['ruling']",
        "subject": "<class 'kodezart.types.domain.amendment.RulingSubject'>",
        "resolution": "<class 'str'>",
        "rejected_alternative": (
            "typing.Optional[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])]]"
        ),
        "repo_evidence": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
    },
    "RulingSubject": {
        "kind": "typing.Literal['ruling']",
        "id": "kodezart.types.domain.ruling_id.RulingId",
    },
    "SherlockFlag": {
        "criterion_id": "typing.Optional[kodezart.types.domain.criteria.CriterionId]",
        "concern": "<class 'str'>",
    },
    "SpecFinding": {
        "issue_id": "<class 'str'>",
        "defect_class": "<class 'str'>",
        "evidence": "<class 'str'>",
        "role": "<enum 'DefectRole'>",
        "mandate_text": "str | None",
    },
    "SplitChildProposal": {
        "deliverable_key": "<class 'str'>",
        "title": "<class 'str'>",
        "body": "<class 'str'>",
    },
    "SplitProposal": {
        "kind": "typing.Literal['split']",
        "issue_id": "<class 'str'>",
        "children": (
            "tuple[kodezart.types.domain.organize_graph.SplitChildProposal, ...]"
        ),
    },
    "TicketDraftOutput": {
        "title": "<class 'str'>",
        "summary": "<class 'str'>",
        "context": "<class 'str'>",
        "references": "list[kodezart.types.domain.agent.CodeReference]",
        "required_changes": "list[kodezart.types.domain.agent.FileChange]",
        "out_of_scope": "list[str]",
        "open_questions": "list[str]",
        "sherlock_flags": "list[kodezart.types.domain.agent.CritiqueFlag]",
    },
    "TicketReviewOutput": {
        "approved": "<class 'bool'>",
        "feedback": "<class 'str'>",
        "suggestions": "list[str]",
    },
    "TrackerArtifact": {
        "surface": "<class 'kodezart.types.domain.surface.WritableSurface'>",
        "native_ref": "<class 'str'>",
        "content": "<class 'str'>",
    },
    "UnavailableProposal": {
        "kind": "typing.Literal['unavailable']",
        "issue_id": "<class 'str'>",
        "capability": "typing.Literal['criterion_edit']",
        "evidence": "<class 'str'>",
    },
    "UnresolvedProposal": {
        "kind": "typing.Literal['unresolved']",
        "issue_id": "<class 'str'>",
        "question": "<class 'str'>",
        "evidence": "<class 'str'>",
    },
    "UnverifiableAdmission": {
        "verdict": "typing.Literal[<AdmissionVerdict.UNVERIFIABLE: 'unverifiable'>]",
        "issue_id": "<class 'str'>",
        "evidence": "<class 'str'>",
        "findings": "tuple[kodezart.types.domain.organize.SpecFinding, ...]",
        "missing_artifact": "<class 'str'>",
        "pending_blocker_id": "<class 'str'>",
    },
    "UpheldAmendment": {
        "verdict": "typing.Literal['upheld']",
        "claim": "<class 'kodezart.types.domain.amendment.AmendmentClaim'>",
        "reason": "<enum 'UpheldReason'>",
        "judgment": "<class 'kodezart.types.domain.amendment.AmendmentJudgment'>",
        "publication": (
            "kodezart.types.domain.amendment.RecordedRefusal | "
            "kodezart.types.domain.amendment.EscalatedRefusal"
        ),
    },
    "UpheldJudgment": {
        "verdict": "typing.Literal['upheld']",
        "claim": "<class 'kodezart.types.domain.amendment.AmendmentClaim'>",
        "reason": "<enum 'UpheldReason'>",
        "judgment": "<class 'kodezart.types.domain.amendment.AmendmentJudgment'>",
    },
    "WriteBackFinding": {
        "verdict": "<enum 'AuditVerdict'>",
        "evidence": "<class 'str'>",
        "cited_refs": (
            "tuple[typing.Annotated[str, FieldInfo(annotation=NoneType, "
            "required=True, metadata=[MinLen(min_length=1), "
            "_PydanticGeneralMetadata(pattern='\\\\S')])], ...]"
        ),
    },
    "WriteBackResult": {
        "verdict": "<enum 'AuditVerdict'>",
        "artifact": "<class 'kodezart.types.domain.audit.TrackerArtifact'>",
        "rounds": "tuple[kodezart.types.domain.write_back.WriteBackFinding, ...]",
    },
}


def test_the_session_roots_are_every_model_a_session_is_handed_as_its_schema():
    """The roots are derived from the code, so a new session output is a new root.

    Every call in ``src/kodezart`` that hands a session a schema through
    ``output_schema`` or through ``output_format``'s ``schema`` is resolved to the
    model that produces that schema. The amendment author's output, the judge's
    judgment and the writer's claims are among them, and every schema handed over
    is some model's, so none is skipped for want of a model to match.
    """
    roots, unmatched = session_roots()
    assert roots
    assert unmatched == []
    assert {AmendmentTextOutput, AmendmentJudgment, NativeWriterOutput} <= set(roots)
    assert {
        AmendmentClaim,
        AmendmentJudgment,
        UpheldJudgment,
        UpheldAmendment,
        NativeWriterOutput,
        FindingEvidence,
        AmendmentTextOutput,
    } <= set(_reachable_models((*roots, *JUDGED_BY)))


#: What every planted module starts with: the schemas and model it hands over,
#: a session call that takes anything, and a judge whose second parameter is the
#: schema. The prelude alone hands nothing over.
PLANTED_PRELUDE = """\
import functools
import importlib
import operator

import kodezart.types.domain.agent as agent
from kodezart.types.domain.agent import AMENDMENT_JUDGMENT_SCHEMA
from kodezart.types.domain.agent import AMENDMENT_TEXT_SCHEMA
from kodezart.types.domain.agent import AMENDMENT_TEXT_SCHEMA as ALIASED
from kodezart.types.domain.amendment_write import AmendmentTextOutput


def run(*args, **kwargs):
    return None


def judge(prompt, output_schema):
    return None


class Judge:
    def judge(self, prompt, output_schema):
        return None
"""

#: One planted body per form the derivation follows, each handing a session the
#: amendment author's schema (or, for the conditional, one of two schemas).
FOLLOWED_FORMS = {
    "imported_name": "def f():\n    run(output_schema=AMENDMENT_TEXT_SCHEMA)\n",
    "import_alias": "def f():\n    run(output_schema=ALIASED)\n",
    "module_attribute": (
        "def f():\n    run(output_schema=agent.AMENDMENT_TEXT_SCHEMA)\n"
    ),
    "module_assignment": "SAME = ALIASED\n\n\ndef f():\n    run(output_schema=SAME)\n",
    "output_format_display": (
        'def f():\n    run(output_format={"type": "json_schema", "schema": ALIASED})\n'
    ),
    "local_binding": (
        'def f():\n    shape = {"schema": ALIASED}\n    run(output_format=shape)\n'
    ),
    "annotated_local_binding": (
        "def f():\n"
        '    shape: dict[str, object] = {"schema": ALIASED}\n'
        "    run(output_format=shape)\n"
    ),
    "walrus": (
        "def f():\n    if (schema := ALIASED):\n        run(output_schema=schema)\n"
    ),
    "walrus_in_the_argument": (
        "def f():\n    run(output_schema=(schema := ALIASED))\n"
    ),
    "closure": (
        "def f():\n"
        "    schema = ALIASED\n"
        "    def g():\n"
        "        run(output_schema=schema)\n"
    ),
    "closure_through_a_nested_class": (
        "def f():\n"
        "    schema = ALIASED\n"
        "    class Inner:\n"
        "        def g(self):\n"
        "            run(output_schema=schema)\n"
    ),
    "literal_getattr": (
        'def f():\n    run(output_schema=getattr(agent, "AMENDMENT_TEXT_SCHEMA"))\n'
    ),
    "attrgetter": (
        "def f():\n"
        '    run(output_schema=operator.attrgetter("AMENDMENT_TEXT_SCHEMA")(agent))\n'
    ),
    "vars_subscript": (
        'def f():\n    run(output_schema=vars(agent)["AMENDMENT_TEXT_SCHEMA"])\n'
    ),
    "dunder_dict_subscript": (
        'def f():\n    run(output_schema=agent.__dict__["AMENDMENT_TEXT_SCHEMA"])\n'
    ),
    "import_module": (
        "def f():\n"
        "    run(output_schema=importlib.import_module(\n"
        '        "kodezart.types.domain.agent"\n'
        "    ).AMENDMENT_TEXT_SCHEMA)\n"
    ),
    "dunder_import": (
        "def f():\n"
        '    run(output_schema=__import__("kodezart.types.domain.agent")\n'
        "        .types.domain.agent.AMENDMENT_TEXT_SCHEMA)\n"
    ),
    "model_json_schema": (
        "def f():\n    run(output_schema=AmendmentTextOutput.model_json_schema())\n"
    ),
    "double_star_display": 'def f():\n    run(**{"output_schema": ALIASED})\n',
    "positional": 'def f():\n    judge("prompt", ALIASED)\n',
    "starred_display": 'def f():\n    judge(*["prompt", ALIASED])\n',
    "method_with_explicit_instance": (
        'def f():\n    Judge.judge(Judge(), "prompt", ALIASED)\n'
    ),
    "bound_method_in_a_local": (
        'def f():\n    bound = Judge().judge\n    bound("prompt", ALIASED)\n'
    ),
    "partial_keyword": (
        "def f():\n    return functools.partial(run, output_schema=ALIASED)\n"
    ),
    "partial_positional": (
        'def f():\n    return functools.partial(judge, "prompt", ALIASED)\n'
    ),
    "lambda_default": "HANDLER = lambda output_schema=ALIASED: output_schema\n",
    "keyword_only_lambda_default": (
        "HANDLER = lambda *, output_schema=ALIASED: output_schema\n"
    ),
    "conditional": (
        "def f(flag):\n"
        "    run(output_schema=(\n"
        "        AMENDMENT_JUDGMENT_SCHEMA if flag else AMENDMENT_TEXT_SCHEMA\n"
        "    ))\n"
    ),
}

#: One planted body per shape in the stated limit. Each hands the same schema
#: over, and none of them is read.
LIMIT_SHAPES = {
    "parameter": "def f(schema):\n    run(output_schema=schema)\n",
    "returned_from_a_helper": (
        "def make():\n    return ALIASED\n\n\ndef f():\n    run(output_schema=make())\n"
    ),
    "stored_on_an_object": (
        "class Holder:\n    def f(self):\n        run(output_schema=self.schema)\n"
    ),
    "container_built_elsewhere": (
        "def options():\n"
        '    return {"schema": ALIASED}\n'
        "\n\n"
        "def f():\n"
        '    run(output_schema=options()["schema"])\n'
    ),
    "method_on_self": (
        "class Caller(Judge):\n"
        "    def f(self):\n"
        '        self.judge("prompt", ALIASED)\n'
    ),
    "name_built_at_run_time": (
        "def f():\n"
        '    run(output_schema=getattr(agent, "AMENDMENT_TEXT" + "_SCHEMA"))\n'
    ),
    "globals_inside_a_function": (
        "def bind():\n"
        '    globals()["LATER"] = ALIASED\n'
        "\n\n"
        "def f():\n"
        "    run(output_schema=LATER)\n"
    ),
    "setattr_inside_a_function": (
        "import sys\n"
        "\n\n"
        "def bind():\n"
        '    setattr(sys.modules[__name__], "LATER", ALIASED)\n'
        "\n\n"
        "def f():\n"
        "    run(output_schema=LATER)\n"
    ),
}


def _planted(tmp_path, body):
    """Import a planted module and return what it hands a session, as models."""
    source = f"{PLANTED_PRELUDE}\n\n{body}"
    path = tmp_path / "planted_session_call.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handed = handed_schemas(ast.parse(source), vars(module))
    return schema_models(handed, (AmendmentJudgment, AmendmentTextOutput))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            body,
            {AmendmentJudgment, AmendmentTextOutput}
            if name == "conditional"
            else {AmendmentTextOutput},
            id=name,
        )
        for name, body in FOLLOWED_FORMS.items()
    ],
)
def test_the_root_derivation_follows_each_form_that_hands_a_schema(
    tmp_path, body, expected
):
    """Each form the derivation reads is seen handing its schema to a session."""
    models, unmatched = _planted(tmp_path, body)
    assert set(models) == expected
    assert unmatched == []


@pytest.mark.parametrize("body", LIMIT_SHAPES.values(), ids=LIMIT_SHAPES.keys())
def test_the_root_derivation_does_not_read_past_its_stated_limit(tmp_path, body):
    """Each shape in the stated limit hands a schema over and is not seen."""
    assert _planted(tmp_path, body) == ([], [])


def test_the_root_derivation_prelude_and_a_cycle_hand_nothing_over(tmp_path):
    """The prelude alone is silent, a cycle of bindings ends, and a bare schema counts.

    A schema no candidate model produces is returned as unmatched, so the
    derivation's own check that every schema handed over is some model's can
    fail.
    """
    assert _planted(tmp_path, "") == ([], [])
    cycle = "def f():\n    a = b\n    b = a\n    run(output_schema=a)\n"
    assert _planted(tmp_path, cycle) == ([], [])
    bare = 'BARE = {"type": "object"}\n\n\ndef f():\n    run(output_schema=BARE)\n'
    assert _planted(tmp_path, bare) == ([], [{"type": "object"}])


def _closure_snapshot(roots):
    """Every field of every model reachable from *roots*, with its declared type."""
    return {
        model.__name__: {
            name: repr(field.annotation) for name, field in model.model_fields.items()
        }
        for model in _reachable_models(roots)
    }


def _core_schema_models(roots):
    """Every model class pydantic's own schema of *roots* validates, roots included.

    Read off each root's core schema, which pydantic builds by resolving every
    annotation form itself, aliases and type variables included, so this walk
    shares nothing with the annotation walk above. The core schema is a finite
    tree of dicts, lists and tuples that names a shared model by a string
    reference; each container is visited once.
    """
    found: list[type[BaseModel]] = []
    visited: set[int] = set()
    pending: list[object] = [root.__pydantic_core_schema__ for root in roots]
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, dict):
            model = node.get("cls")
            if node.get("type") == "model" and model not in found:
                found.append(model)
            pending.extend(node.values())
        elif isinstance(node, list | tuple):
            pending.extend(node)
    return found


def test_every_model_a_session_fills_is_inside_the_snapshot():
    """No session-filled model sits outside the snapshot, however it is reached.

    Every model class that pydantic's own schema of a derived root validates
    is a key of the snapshot: one named through a PEP 695 alias, a type
    variable, a union, a discriminator or a root model is pinned with its
    fields like any other.
    """
    roots, _ = session_roots()
    reached = _core_schema_models(roots)
    assert {model.__name__ for model in reached} >= {
        "AuditMandateJudgment",
        "MandateInstructed",
        "MandateAbsent",
        "MandateUnverifiable",
        "MandateFinding",
    }
    assert {model.__name__ for model in reached} - set(SESSION_CLOSURE) == set()


def _planted_root(form, gained):
    """A root that reaches one model only through *form*; *gained* adds a field.

    The added field is the shape of a probe outcome, a capability paired with
    a truth value.
    """
    fields: dict[str, object] = {"evidence": (str, ...)}
    if gained:
        fields["observed"] = (tuple[tuple[str, bool], ...], ())
    inner = create_model("PlantedFinding", **fields)
    if form == "alias":
        alias = TypeAliasType("PlantedJudgment", Annotated[inner | None, "planted"])
        return RootModel[alias]
    return create_model(
        "PlantedHolder", judgment=(TypeVar("PlantedVerdict", bound=inner), ...)
    )


@pytest.mark.parametrize("form", ["alias", "type_variable_bound"])
def test_a_model_reached_only_through_an_alias_gaining_a_field_fails_the_snapshot(
    form,
):
    """The snapshot walk follows what ``get_args`` does not reach.

    A root whose only route to a model is a PEP 695 alias, or a type variable's
    bound, is pinned with that model's fields, so the model gaining a field
    changes the snapshot.
    """
    kept = _closure_snapshot((_planted_root(form, gained=False),))
    gained = _closure_snapshot((_planted_root(form, gained=True),))
    assert kept["PlantedFinding"] == {"evidence": "<class 'str'>"}
    assert gained["PlantedFinding"] == {
        "evidence": "<class 'str'>",
        "observed": "tuple[tuple[str, bool], ...]",
    }
    assert gained != kept


def test_no_field_can_carry_a_session_observed_probe_outcome():
    """A probe outcome is a capability paired with a truth value about this host.

    Undemonstrability is resolved from configuration alone, so no model a session
    fills in may offer a seat for what a session claims to have observed about
    this environment. The session-facing set is the closure of every model
    reachable from the derived session roots and from the verdicts a judgment is
    judged by, nested ones included. Every field of every model in that closure
    is pinned with its declared type, so a field added, removed or retyped on a
    root, a citation, a demonstration or a cost claim cannot pass unnoticed.

    The snapshot is the net because the shape of a probe outcome cannot be told
    apart from legitimate session-observed truth values by type alone: a
    demonstration's satisfied_at_base and a cost measurement's affordable are
    both booleans a session reports. The derived clauses below stay as a second
    net and as the reason a new field must be read before the snapshot is
    widened: across the closure the only field that mentions the capability
    vocabulary at all is the typed claim, which names a capability and no truth
    value; no field in the closure is a mapping to truth values, whatever it is
    keyed by; and across the models declared in `kodezart.types.domain` the only
    field pairing capabilities with truth values is the repository's own
    declared environment, which is configuration.
    """
    roots, _ = session_roots()
    session_models = _reachable_models((*roots, *JUDGED_BY))
    assert session_models
    assert len({model.__name__ for model in session_models}) == len(session_models)
    assert _closure_snapshot((*roots, *JUDGED_BY)) == SESSION_CLOSURE
    session_facing = {
        (model.__name__, name): field.annotation
        for model in session_models
        for name, field in model.model_fields.items()
    }
    assert {
        address: str(annotation)
        for address, annotation in session_facing.items()
        if any(found is CheckPrerequisite for found in _annotation_types(annotation))
    } == {
        ("AmendmentClaim", "claimed_capability"): (
            "kodezart.types.domain.operation.CheckPrerequisite | None"
        )
    }
    assert {
        address: str(annotation)
        for address, annotation in session_facing.items()
        if any(
            _is_truth_valued_mapping(found) for found in _annotation_types(annotation)
        )
    } == {}
    assert {
        (model.__name__, name)
        for model in _declared_models()
        for name, field in model.model_fields.items()
        if {CheckPrerequisite, bool} <= set(_annotation_types(field.annotation))
    } == {("RepoEntry", "runner_environment")}


def test_upheld_record_cannot_misattribute_a_judgment_or_claim_amended():
    value = record().model_dump()
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(value | {"verdict": "amended"})
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(
            value
            | {"claim": value["claim"] | {"subject": {"kind": "ruling", "id": "other"}}}
        )


def test_actual_scope_egress_roundtrips_required_nulls_and_rejects_bad_native_reports():
    event = ScopeLaneEvent(
        lane_key="lane",
        event=NativeAmendmentEvent(
            report=AmendmentReport(verdicts=(record(),)),
        ),
    )
    payload = _queued_event_payload(event)
    assert ScopeLaneEvent.model_validate(payload) == event
    original = payload["event"]["report"]["verdicts"][0]
    assert "claimedCapability" in original["claim"]
    assert original["claim"]["claimedCapability"] is None
    original["reason"] = "guessed_reason"
    with pytest.raises(ValidationError):
        ScopeLaneEvent.model_validate(payload)


@pytest.mark.parametrize(
    "claimed,environment,expected",
    [
        (None, {}, "ground_not_reproduced"),
        ("network", None, "ground_not_reproduced"),
        ("network", {CheckPrerequisite.NETWORK: True}, "ground_not_reproduced"),
        ("network", {CheckPrerequisite.NETWORK: False}, "environment_lacks_capability"),
        # Only an explicit false declares a capability absent. A declaration that
        # says nothing about it leaves it unknown, so the criterion stands.
        pytest.param(
            "network",
            {},
            "ground_not_reproduced",
            id="an_undeclared_capability_is_unknown_not_absent",
        ),
        # The claimed capability is matched to its own declared entry: another
        # capability's absence says nothing about the one claimed.
        pytest.param(
            "credentials",
            {CheckPrerequisite.NETWORK: False},
            "ground_not_reproduced",
            id="another_capability_declared_absent_leaves_the_claimed_one_unknown",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: True, CheckPrerequisite.CREDENTIALS: False},
            "ground_not_reproduced",
            id="the_claimed_capability_declared_present_beside_an_absent_one",
        ),
        pytest.param(
            "credentials",
            {CheckPrerequisite.NETWORK: True, CheckPrerequisite.CREDENTIALS: False},
            "environment_lacks_capability",
            id="the_claimed_capability_declared_absent_beside_a_present_one",
        ),
    ],
)
def test_missing_capability_requires_a_typed_claim_absent_from_declared_capabilities(
    claimed,
    environment,
    expected,
):
    value = record()
    claim = AmendmentClaim.model_validate(
        value.claim.model_dump() | {"claimed_capability": claimed}
    )
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump()
        | {
            "finding": {
                "verdict": "unverifiable",
                "smallest_repair": "environment_supply",
                "missing_resource": "Network",
            }
        }
    )
    assert upheld_reason(claim, judgment, environment=environment).value == expected


@pytest.mark.parametrize(
    "affordable,measured,cited,expected",
    [
        (True, True, True, "cost_measured_affordable"),
        (False, True, True, "cost_measured_uneconomic"),
        (False, False, True, "ground_not_reproduced"),
        (False, True, False, "ground_not_reproduced"),
    ],
)
def test_cost_never_authorizes_amendment_and_requires_recorded_base_measurement(
    affordable,
    measured,
    cited,
    expected,
):
    value = record()
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump()
        | {
            "finding": {
                "verdict": "feasible",
                "smallest_repair": "none",
                "cost_claim": {
                    "assertion": "Measured cost at base",
                    "measurement": {
                        "observed": "Actual measurement output",
                        "affordable": affordable,
                    }
                    if measured
                    else None,
                },
            },
            "measured_by": "Reproduced recorded command at base" if measured else None,
            "citations": [
                {"path": "measurements.txt", "quote": "Actual measurement output"}
            ]
            if cited
            else [],
        }
    )
    assert upheld_reason(value.claim, judgment, environment={}).value == expected


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("drop_measurement", "retains the actual measurement"),
        ("drop_measured_by", "retains the actual measurement"),
        ("omit_measured_by", "Field required"),
        ("contradicting_affordability", "match the measured affordability"),
        ("contradicting_uneconomic", "must match the measured affordability"),
    ],
)
def test_a_measured_cost_reason_keeps_its_measurement_and_never_authorizes_an_amendment(
    mutation, message
):
    """The record shape behind the three arms, at the level the arms are stored.

    A measured-cost reason cannot be stored without both halves of the
    measurement — what was observed and how it was produced — and cannot be
    stored against an affordability the measurement contradicts. The closing half
    is that no measured cost reaches the applied form at all.

    The two measured-by rows are one clause read twice, because they fail for
    different reasons: an explicit None is refused by the rule about the two
    halves, while an absent key is refused only because the field is required.
    A field handed a default would keep refusing the None and quietly accept
    the absence, which is a record stored without how its measurement was
    produced.

    The contradicting row flips the measured affordability and leaves the reason
    alone on purpose: flipping the reason instead would also trip the completed
    refusal's publication rule, and which validator speaks first is not something
    this assertion depends on. The reverse row does flip the reason, to the
    uneconomic one over an affordable measurement, and escalates it, so the
    publication rule is satisfied and only the affordability rule can speak.
    """
    value = record(reason="cost_measured_affordable").model_dump()
    cost = value["judgment"]["finding"]["cost_claim"]
    if mutation == "drop_measurement":
        cost["measurement"] = None
    elif mutation == "drop_measured_by":
        value["judgment"]["measured_by"] = None
    elif mutation == "omit_measured_by":
        del value["judgment"]["measured_by"]
    elif mutation == "contradicting_uneconomic":
        value["reason"] = "cost_measured_uneconomic"
        value["publication"] = {
            "kind": "escalated",
            "record": value["publication"]["record"],
            "escalation": value["publication"]["record"],
        }
    else:
        cost["measurement"]["affordable"] = False
    with pytest.raises(ValidationError) as failure:
        UpheldAmendment.model_validate(value)
    assert message in str(failure.value)

    applied = amended().model_dump()
    applied["judgment"]["finding"]["cost_claim"] = {
        "assertion": "A measured cost",
        "measurement": {"observed": "Executed once at base", "affordable": True},
    }
    with pytest.raises(ValidationError, match="requires its own reproduced judgment"):
        AmendedAmendment.model_validate(applied)


#: The rule each anchored row must be refused by; the other rows are refused by
#: whichever rule their mutation breaks.
REFUSING_RULE = {
    "environment_without_escalation": (
        "only a refusal at an escalating reason carries escalation"
    ),
    "ground_with_escalation": (
        "only a refusal at an escalating reason carries escalation"
    ),
    "environment_without_capability": "requires the typed claimed capability",
}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_publication",
        "unverified",
        "wrong_issue",
        "duplicate",
        "uneconomic_without_escalation",
        "environment_without_escalation",
        "ground_with_escalation",
        "environment_without_capability",
    ],
)
def test_completed_reports_refuse_missing_or_unrelated_canonical_evidence(mutation):
    """The publication rule is a biconditional, refused from both directions.

    Two rows are the controls the set rule needs. The one withholding the
    escalation carries the typed claimed capability, so the subject rule cannot
    speak first and only the publication rule can refuse; the one adding an
    escalation leaves the reason a person is never asked about, so an escalation
    on it is a question with no addressee.

    The last row is the fail-closed half: a missing-capability reason with no
    capability to name is refused whatever its publication carries, and it is
    escalated here so that the publication rule cannot be what speaks.
    """
    value = record().model_dump()
    if mutation == "missing_publication":
        del value["publication"]
    elif mutation == "unverified":
        value["publication"]["record"]["verdict"] = "unverifiable"
    elif mutation == "wrong_issue":
        value["publication"]["record"]["artifact"]["surface"]["ref"]["key"] = (
            "another-issue"
        )
    elif mutation == "uneconomic_without_escalation":
        value["reason"] = "cost_measured_uneconomic"
    elif mutation == "environment_without_escalation":
        value["reason"] = "environment_lacks_capability"
        value["claim"]["claimed_capability"] = "network"
    elif mutation == "ground_with_escalation":
        value["publication"] = {
            "kind": "escalated",
            "record": value["publication"]["record"],
            "escalation": value["publication"]["record"],
        }
    elif mutation == "environment_without_capability":
        # Escalated, so the publication rule passes and the only rule left to
        # speak is the one requiring the capability this reason names.
        value["reason"] = "environment_lacks_capability"
        value["publication"] = {
            "kind": "escalated",
            "record": value["publication"]["record"],
            "escalation": value["publication"]["record"],
        }
    with pytest.raises(ValidationError, match=REFUSING_RULE.get(mutation)):
        AmendmentReport.model_validate(
            {"verdicts": [value, value] if mutation == "duplicate" else [value]}
        )
