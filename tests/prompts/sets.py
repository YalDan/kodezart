"""The shipped prompt sets, and the fixture cases every suite renders them with.

Each set is a complete corpus authored for one model and held to the same
rules as every other; none is a frozen copy of another, and nothing here
pins bytes.  What
is shared is the CASE roster — one fixed set of variables per function
key — so a suite that renders either set renders it the way every other
suite does, and a difference between two renders is a difference of
authoring rather than of test setup.
"""

from kodezart.adapters.in_repo_prompt_registry import InRepoPromptRegistry
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.domain.prompt_variables import (
    execution_criteria_variables,
    scope_variables,
)
from kodezart.domain.rulings import EMPTY_REGISTRY, pinned_registry
from kodezart.services.prompt_pass import gate_render_bindings
from kodezart.types.domain.agent import Ruling
from kodezart.types.domain.amendment import AmendmentClaim, AmendmentJudgment
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding
from tests.domain.test_rulings import ruling_data
from tests.fakes import FIXTURE_EPOCH, make_tracker_issue, pass_render_variables
from tests.prompts.test_prompt_wiring import (
    CRITERIA,
    DEFAULT_SET,
    RENDER_CASES,
    REPO_ROOT,
    TASK_MD,
    load_registry,
)

#: A set is a corpus authored for a MODEL, and which one runs is chosen for
#: the model in use: ``anthropic_v5`` is the configured default today,
#: ``claude-opus`` is the set for that engine, and a third engine gets a
#: third directory (KOD-306).  ``DEFAULT_SET`` in the wiring suite names
#: ``claude-opus`` — the name is the migration's, from when it was.
V5_SET = "anthropic_v5"
OPUS_SET = DEFAULT_SET

EXAMPLE_OPERATION = REPO_ROOT / "docs" / "operation.example.toml"

#: What the RUNNER binds when it fires a pass and nothing else does: the
#: per-run record title every pass template carries (KOD-290, KOD-306).  A
#: suite that renders a pass template directly binds this, the way the
#: service does through ``pass_render_bindings``.
PER_RUN: dict[str, object] = {"record_title": "pass — fixture @ the pass start"}

AUDITED_PAYLOAD = "Golden payload under audit.\nSecond line."
AUDIT_DESTINATION = "a public code-hosting surface"

#: The keys the wiring suite's roster leaves to the operation namespace:
#: rendered from the same kind of fixed fixtures, plus the operation the
#: two pass keys and the knowledge map address.
ORGANIZE_CASE: dict[str, object] = {
    "organize_context": "Golden current native graph and recorded rulings",
    "mandate_rubric": "Golden mandate rubric",
    "issue_body": "Golden source issue body",
    "issue_key": "external/42",
    "linked_issue_bodies": ["Golden linked issue body"],
    "criterion_issue_bodies": ["Golden criterion issue body"],
    "refusal_evidence": "Golden refusal evidence",
    "defect_classes": ["Golden defect class"],
    "base_ref": "main",
}

#: The native amendment path's shared records.  Every value below is bound the
#: way its caller binds it: the model's own JSON, never free text standing in.
BASE_SHA = "a" * 40
PINNED_RULING = Ruling.model_validate(ruling_data())
AMENDMENT_CLAIM = AmendmentClaim(
    subject={"kind": "criterion", "id": "external/check"},
    stage="implementation",
    ground="unsatisfiable_at_base",
    departure="A proposed departure",
    claimed_capability=None,
)
CRITERION_SURFACE = WritableSurface(
    kind=SurfaceKind.CRITERION_SUB_ISSUE,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key="external/check"),
)
WRITTEN_ARTIFACT = TrackerArtifact(
    surface=CRITERION_SURFACE,
    native_ref="external/check",
    content="**Check:** the observable fixture Check",
)

EXTENDED_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    "audit_overclaim": (
        PromptKey.AUDIT_OVERCLAIM,
        {
            "criterion_key": "external/check",
            "graded_sha": "graded-commit",
            "head_sha": "exact-head",
            "check": "A source-addressed claim.",
        },
    ),
    "audit_mandate": (
        PromptKey.AUDIT_MANDATE,
        {
            "defect_class": "unsupported claim",
            "refutation_evidence": "Observed counterexample.",
            "head_sha": "exact-head",
            "audited_surfaces": "[]",
        },
    ),
    "audit_claim": (
        PromptKey.AUDIT_CLAIM,
        {
            "criterion_key": "external/check",
            "head_sha": "exact-head",
            "check": "A behavioral claim.",
        },
    ),
    "audit_detection_removal": (
        PromptKey.AUDIT_DETECTION_REMOVAL,
        {
            "criterion_key": "external/check",
            "graded_sha": "graded-commit",
            "head_sha": "current-commit",
            "check": "A behavioral claim.",
        },
    ),
    "organize_assess": (PromptKey.ORGANIZE_ASSESS, ORGANIZE_CASE),
    "organize_author": (PromptKey.ORGANIZE_AUTHOR, ORGANIZE_CASE),
    "organize_verify": (PromptKey.ORGANIZE_VERIFY, ORGANIZE_CASE),
    "organize_criteria_author": (PromptKey.ORGANIZE_CRITERIA_AUTHOR, ORGANIZE_CASE),
    "organize_spec_rubric": (PromptKey.ORGANIZE_SPEC_RUBRIC, {}),
    #: services/organize_session_owner.py ``render``: the scope, the phase's
    #: marker, the members that owe it and which phase's rubric runs.
    "organize_session": (
        PromptKey.ORGANIZE_SESSION,
        {
            "scope_key": "golden-project",
            "scope_project": True,
            "scope_issue": None,
            "scope_initiative": None,
            "scope_milestone": None,
            "phase_marker": "body complete",
            "owed_members": ("external/42", "external/43"),
            "phase_ticket": True,
        },
    ),
    "content_audit": (
        PromptKey.CONTENT_AUDIT,
        {"content": AUDITED_PAYLOAD, "destination": AUDIT_DESTINATION},
    ),
    "knowledge_map": (PromptKey.KNOWLEDGE_MAP, {}),
    "fire_record": (PromptKey.FIRE_RECORD, PER_RUN),
    "fire_time_ruling": (
        PromptKey.FIRE_TIME_RULING,
        {
            "issue_key": "external/42",
            "task_md": TASK_MD,
            "pinned_rulings": EMPTY_REGISTRY,
        },
    ),
    "fire_prep_pass": (
        PromptKey.FIRE_PREP_PASS,
        pass_render_variables(PromptKey.FIRE_PREP_PASS),
    ),
    "grooming_pass": (
        PromptKey.GROOMING_PASS,
        pass_render_variables(PromptKey.GROOMING_PASS),
    ),
    #: services/prompt_pass.py ``PromptPass._ask``: the pass the question is
    #: for and where its window starts, the two per-tick values.
    "pass_gate": (
        PromptKey.PASS_GATE,
        gate_render_bindings(
            name=PromptKey.FIRE_PREP_PASS.value, window_start=FIXTURE_EPOCH
        ),
    ),
    #: The cron's scan binds nothing per call: the boundary is the operation's.
    "scope_scan": (PromptKey.SCOPE_SCAN, {}),
    #: The run's check binds the parent it asks about.
    "scope_done": (
        PromptKey.SCOPE_DONE,
        scope_variables(ScopeRef(kind=ScopeKind.PROJECT, key="golden-project")),
    ),
    "remediation_ticket": (
        PromptKey.REMEDIATION_TICKET,
        {
            "original_ticket": TASK_MD,
            "done_work": "golden done work",
            "failure_evidence": "golden failure evidence",
        },
    ),
    #: The removal session's own member: it binds the criteria roster and
    #: nothing else, so the same variables the evaluator's case is built from
    #: serve it.
    "mutation_survival": (
        PromptKey.MUTATION_SURVIVAL,
        execution_criteria_variables(CRITERIA),
    ),
    #: The base reading's member binds what ``RalphLoop._checks_at_base``
    #: binds: the passing criteria's roster and the full commit the lane's
    #: recorded base resolved to.
    "base_check": (
        PromptKey.BASE_CHECK,
        {**execution_criteria_variables(CRITERIA), "base_sha": "b" * 40},
    ),
    #: services/native_amendments.py ``begin``: the pinned registry as read back.
    "native_writer_contract": (
        PromptKey.NATIVE_WRITER_CONTRACT,
        {"pinned_rulings": pinned_registry((PINNED_RULING,))},
    ),
    #: services/native_amendments.py ``_judge_claim``.
    "amendment_judge": (
        PromptKey.AMENDMENT_JUDGE,
        {
            "claim": AMENDMENT_CLAIM.model_dump_json(),
            "criteria": make_tracker_issue(
                "external/check",
                parent_key="external/42",
                issue_labels=frozenset({"criterion"}),
                body="**Check:** the observable fixture Check",
            ).model_dump_json(),
            "pinned_rulings": PINNED_RULING.model_dump_json(),
            "base_sha": BASE_SHA,
        },
    ),
    #: services/amendment_writeback.py ``_author``, on a repair round: the
    #: prior write-back finding is present, so it binds the finding's JSON.
    "amendment_author": (
        PromptKey.AMENDMENT_AUTHOR,
        {
            "claim": AMENDMENT_CLAIM.model_dump_json(),
            "judgment": AmendmentJudgment(
                subject=AMENDMENT_CLAIM.subject,
                base_sha=BASE_SHA,
                ground=AMENDMENT_CLAIM.ground,
                reproduced=True,
                finding={
                    "verdict": "infeasible",
                    "smallest_repair": "criterion_text",
                    "refutation": "No implementation at base satisfies the text.",
                },
                citations=({"path": "policy.py", "quote": "def answer(): return 42"},),
                measured_by=None,
            ).model_dump_json(),
            "prior": WRITTEN_ARTIFACT.model_dump_json(),
            "finding": WriteBackFinding(
                verdict="refuted",
                evidence="The amended Check names a test that does not exist.",
                cited_refs=("tests/real.py",),
            ).model_dump_json(),
            "preserve_subject": "false",
        },
    ),
    #: chains/write_back_verifier.py ``judge``: the exact commit and the
    #: artifact re-read after the write.
    "write_back_verify": (
        PromptKey.WRITE_BACK_VERIFY,
        {
            "written_artifact": WRITTEN_ARTIFACT.model_dump_json(by_alias=True),
            "base_ref": BASE_SHA,
        },
    ),
}

ALL_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    **RENDER_CASES,
    **EXTENDED_CASES,
}


def operation_registry(*, default_set: str = OPUS_SET) -> InRepoPromptRegistry:
    """A set with the example operation namespace bound.

    The extra names are additive: a template that references none of them
    renders exactly as it does under the wiring suite's empty bindings,
    which is what lets one registry serve every case.
    """
    return load_registry(
        default_set=default_set,
        bindings=dict(operation_bindings(load_operation_config(EXAMPLE_OPERATION))),
    )


def v5_registry() -> InRepoPromptRegistry:
    """The configured default set, with the same operation namespace bound."""
    return operation_registry(default_set=V5_SET)


def render_case(registry: InRepoPromptRegistry, case: str) -> str:
    """Render one case with the skills fragment bound EMPTY."""
    key, variables = ALL_CASES[case]
    return registry.template_for(key).render({**variables, "skills_reference": ""})


def render_v5_case(case: str) -> str:
    """Render one case against the configured default set, skills bound EMPTY."""
    return render_case(v5_registry(), case)


def render_case_with_declared_skills(registry: InRepoPromptRegistry, case: str) -> str:
    """Render one case with the key's DECLARED skills loadout bound."""
    key, variables = ALL_CASES[case]
    return registry.template_for(key).render(variables)
