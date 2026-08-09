"""OperationConfig, the pass templates, and the traceability artifact (KOD-50)."""

import re
from datetime import date
from pathlib import Path

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.config import AppConfig
from kodezart.core.errors import (
    OperationConfigError,
    PromptNamespaceCollisionError,
    PromptRenderError,
)
from kodezart.core.prompt_namespaces import (
    PER_CALL_VARIABLE_NAMES,
    SET_FRAGMENT_NAMES,
    assert_namespaces_disjoint,
    bindings_for,
    operation_bindings,
)
from kodezart.core.prompt_rendering import binding_names, free_binding_names
from kodezart.types.domain.gating import (
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import (
    CHECKPOINT_DOCUMENT_KEY,
    RUN_LOG_RECORD_KEY,
    LifecycleStage,
    OperationConfig,
    PrincipalRole,
    QueueState,
)
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"
CUTOVER = REPO_ROOT / "docs" / "cutover_mapping.md"
SET_DIR = REPO_ROOT / "src" / "kodezart" / "prompts" / "sets" / "claude-opus"
PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)

# Names a shipped template references from inside an ``{{#each}}`` frame
# WITHOUT the ``this.`` root, so the renderer resolves them off the current
# item while the static reader cannot tell them from a free binding. They are
# members of a per-call value, never OperationConfig paths, and every helper
# that reads templates statically has to discount them the same way.
ITEM_SCOPED_NAMES = frozenset({"criterion", "reasoning"})

# Frequency words a cadence-agnostic template may not contain: scheduling
# lives exclusively in scheduler configuration.
# Resolved org-shaped values a legal in-repo template may not contain:
# identities, endpoints, repository names, document titles, dates.
ORG_SHAPED_PATTERNS: dict[RedactionCategory, list[str]] = {
    RedactionCategory.EMAIL_HANDLES: [
        r"[\w.+-]+@[\w-]+\.[\w.]+",
        r"(?<![\w/])@[\w-]{3,}",
    ],
    RedactionCategory.INFRA_ENDPOINTS: [r"https?://"],
    RedactionCategory.CROSS_REPO_NAMES: [r"\b\d{4}-\d{2}-\d{2}\b"],
}

CADENCE_WORDS = (
    "hourly",
    "daily",
    "weekly",
    "nightly",
    "monthly",
    "every hour",
    "every day",
    "every week",
    "twice a day",
    "cron",
    "schedule",
)


def example_config() -> OperationConfig:
    """The shipped annotated example, loaded and structurally validated."""
    return load_operation_config(EXAMPLE)


def raw_example() -> dict[str, object]:
    """The example config as a plain dict, for mutation in failure tests."""
    import tomllib

    return tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))


def markdown_rows(heading: str) -> list[list[str]]:
    """Parse the body rows of the markdown table under *heading*."""
    text = CUTOVER.read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("|- "):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows[1:]


# ---------------------------------------------------------------------------
# AC-3a / D-1 — the typed model
# ---------------------------------------------------------------------------


def test_all_fourteen_fields_are_present_with_the_stated_types() -> None:
    """Field-by-field census: exact equality, never a subset check.

    Grew by ``records`` under KOD-112 R3 fix 6 (the write-side destination
    registry) and by ``private_surface`` under KOD-106 R2 (the operator's
    prose description of what this operation treats as private).  The
    census stays total and stays ``==``; a census loosened to a containment
    check stops being one.
    """
    fields = OperationConfig.model_fields
    assert set(fields) == {
        "operation_name",
        "workspace",
        "principals",
        "agent_identities",
        "teams",
        "queue_states",
        "workflow_states",
        "repos",
        "documents",
        "records",
        "knowledge",
        "endpoints",
        "initiatives",
        "private_surface",
    }
    config = example_config()
    assert isinstance(config.operation_name, str)
    assert isinstance(config.workspace, str)
    assert isinstance(config.queue_states, dict)
    assert set(config.workflow_states) == set(LifecycleStage)
    assert config.initiatives[0].target_date == date(2026, 12, 31)
    assert config.repos[0].checks
    assert config.records[RUN_LOG_RECORD_KEY].append_only is True


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    """extra="forbid" closes the model."""
    raw = raw_example()
    raw["unexpected_field"] = "x"
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(_write_toml(tmp_path, raw))
    assert any("unexpected_field" in failure for failure in excinfo.value.failures)


# ---------------------------------------------------------------------------
# AC-3b / AC-4a — authority binds to a role
# ---------------------------------------------------------------------------


def test_authority_is_read_from_the_role_never_a_name() -> None:
    """The approver is found by role, not by matching a literal."""
    config = example_config()
    assert PrincipalRole.APPROVER in config.approver().roles


def test_no_principal_name_literal_appears_in_code_or_templates() -> None:
    """AC-3b: no principal name is hardcoded anywhere."""
    names = {p.tracker_user for p in example_config().principals}
    src = REPO_ROOT / "src" / "kodezart"
    for path in [*src.rglob("*.py"), *SET_DIR.glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        for name in names:
            assert name not in text, f"{path} names a principal"


@pytest.mark.parametrize("approver_count", [0, 2])
def test_exactly_one_approver_is_enforced_at_config_load(
    approver_count: int,
    tmp_path: Path,
) -> None:
    """Zero approvers and two approvers each raise the typed error."""
    raw = raw_example()
    principals: list[dict[str, str]] = [
        {
            "tracker_user": f"user-{i}",
            "roles": ["approver", "principal", "assignee"],
            "handle": f"@user-{i}",
        }
        for i in range(approver_count)
    ]
    principals.append(
        {"tracker_user": "user-x", "roles": ["principal"], "handle": "@user-x"},
    )
    raw["principals"] = principals
    path = _write_toml(tmp_path, raw)
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(path)
    assert any("APPROVER" in failure for failure in excinfo.value.failures)


# ---------------------------------------------------------------------------
# AC-3c / D-5 — states resolve through their mappings
# ---------------------------------------------------------------------------


def test_no_label_or_status_literal_lives_in_source() -> None:
    """Code addresses members via enum members, never via a label string."""
    src = REPO_ROOT / "src" / "kodezart"
    labels = {
        *example_config().queue_states.values(),
        *example_config().workflow_states.values(),
    }
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for label in labels:
            assert label not in text, f"{path} hardcodes {label!r}"


def test_queue_states_accepts_an_added_member_as_pure_configuration(
    tmp_path: Path,
) -> None:
    """D-5: a new member is a config entry — no type or consumer change."""
    raw = raw_example()
    raw["queue_states"]["escalated"] = "queue:escalated"
    config = load_operation_config(_write_toml(tmp_path, raw))
    assert config.queue_states["escalated"] == "queue:escalated"
    bindings = operation_bindings(config)
    queue = bindings["queue_states"]
    assert isinstance(queue, dict)
    assert queue["escalated"] == "queue:escalated"


@pytest.mark.parametrize("member", list(QueueState))
def test_every_required_queue_member_is_validated_present(
    member: QueueState,
    tmp_path: Path,
) -> None:
    """The five members code addresses by name must exist."""
    raw = raw_example()
    del raw["queue_states"][member.value]
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(_write_toml(tmp_path, raw))
    assert any(member.value in failure for failure in excinfo.value.failures)


@pytest.mark.parametrize("stage", list(LifecycleStage))
def test_workflow_states_are_validated_exactly_parallel_to_queue_states(
    stage: LifecycleStage,
    tmp_path: Path,
) -> None:
    """D-1: workflow_states gets the same boot validation."""
    raw = raw_example()
    del raw["workflow_states"][stage.value]
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(_write_toml(tmp_path, raw))
    assert any(stage.value in failure for failure in excinfo.value.failures)


# ---------------------------------------------------------------------------
# AC-4b — structural failures collect into one error
# ---------------------------------------------------------------------------


def test_multiple_distinct_structural_failures_land_in_one_error(
    tmp_path: Path,
) -> None:
    """Collect-all, not fail-on-first."""
    raw = raw_example()
    raw["principals"] = [
        {"tracker_user": "u", "roles": ["principal"], "handle": "@u"},
    ]
    del raw["queue_states"][QueueState.TRIAGE.value]
    del raw["workflow_states"][LifecycleStage.DONE.value]
    del raw["documents"][CHECKPOINT_DOCUMENT_KEY]
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(_write_toml(tmp_path, raw))
    joined = " ".join(excinfo.value.failures)
    assert "APPROVER" in joined
    assert QueueState.TRIAGE.value in joined
    assert LifecycleStage.DONE.value in joined
    assert CHECKPOINT_DOCUMENT_KEY in joined


# ---------------------------------------------------------------------------
# AC-6 / D-2 — the pointer and the secrets invariant
# ---------------------------------------------------------------------------


def test_app_config_gains_one_pointer() -> None:
    """D-2: exactly one AppConfig field points at the operation file."""
    assert AppConfig().operation_config is None


def test_missing_operation_config_file_is_a_typed_startup_error(
    tmp_path: Path,
) -> None:
    """A pointer at a missing file fails loudly."""
    with pytest.raises(OperationConfigError):
        load_operation_config(tmp_path / "absent.toml")


def test_invalid_toml_is_a_typed_startup_error(tmp_path: Path) -> None:
    """A malformed file fails loudly."""
    path = tmp_path / "bad.toml"
    path.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(OperationConfigError):
        load_operation_config(path)


def test_a_secret_key_in_the_operation_file_fails_validation(
    tmp_path: Path,
) -> None:
    """AC-6: secrets stay env-only in AppConfig; extra="forbid" enforces it."""
    raw = raw_example()
    raw["tracker_token"] = "secret-value"
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(_write_toml(tmp_path, raw))
    assert any("tracker_token" in failure for failure in excinfo.value.failures)


def test_no_new_dependency_was_added_for_toml() -> None:
    """D-2: stdlib tomllib only."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for banned in ("toml =", "tomli", "tomlkit"):
        assert banned not in pyproject


def test_no_deployment_knob_lives_in_the_operation_config() -> None:
    """D-4: placement review — infra and secrets belong to AppConfig."""
    fields = set(OperationConfig.model_fields)
    for deployment_knob in ("github_token", "checkpoint_url", "model", "prompt_set"):
        assert deployment_knob not in fields


def test_operation_config_references_no_tracker_vendor_type() -> None:
    """D-4: OperationConfig stays tracker-agnostic."""
    source = (
        REPO_ROOT / "src" / "kodezart" / "types" / "domain" / "operation.py"
    ).read_text(encoding="utf-8")
    for vendor in ("linear", "jira", "github", "asana"):
        assert vendor not in source.lower()


# ---------------------------------------------------------------------------
# AC-5a / AC-5b — binding sources
# ---------------------------------------------------------------------------


def test_an_unconditional_placeholder_without_a_config_value_fails_loudly() -> None:
    """All missing names collect into one error."""
    template = load_registry().template_for(PromptKey.FIRE_PREP_PASS)
    with pytest.raises(PromptRenderError) as excinfo:
        template.render({})
    assert "operation_name" in excinfo.value.missing
    assert "workspace" in excinfo.value.missing
    assert len(excinfo.value.missing) > 2


def test_a_conditional_only_reference_is_not_reported_missing() -> None:
    """Scoped: only UNCONDITIONAL references are collected."""
    template = load_registry().template_for(PromptKey.FIX)
    rendered = template.render({"task_md": "t", "skills_reference": ""})
    assert "## Review Failures" not in rendered


def test_the_three_binding_namespaces_are_disjoint_at_boot() -> None:
    """AC-5b: the shipped configuration satisfies the assertion."""
    bindings = bindings_for(example_config())
    assert set(bindings) & PER_CALL_VARIABLE_NAMES == set()
    assert set(bindings) & SET_FRAGMENT_NAMES == set()


def test_a_namespace_collision_trips_the_boot_assertion() -> None:
    """A colliding fixture is rejected, naming the collision."""
    with pytest.raises(PromptNamespaceCollisionError) as excinfo:
        assert_namespaces_disjoint(["task_md", "operation_name"])
    assert excinfo.value.colliding == ("task_md",)


def test_per_call_namespace_covers_every_non_operation_template_name() -> None:
    """The declared per-call set is not allowed to drift from the templates."""
    registry = load_registry()
    operation_names = set(bindings_for(example_config()))
    for key in PromptKey:
        names = binding_names(registry.template_for(key).body)
        unaccounted = (
            names
            - operation_names
            - PER_CALL_VARIABLE_NAMES
            - SET_FRAGMENT_NAMES
            - ITEM_SCOPED_NAMES
        )
        unaccounted = {n for n in unaccounted if "." not in n}
        assert unaccounted == set(), f"{key.value} references {unaccounted}"


# ---------------------------------------------------------------------------
# D-3 — the pass templates land in the registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PASS_KEYS)
def test_pass_templates_resolve_through_the_port_and_render(
    key: PromptKey,
) -> None:
    """Both pass templates resolve by key and render from OperationConfig."""
    registry = load_registry(bindings=dict(bindings_for(example_config())))
    rendered = registry.template_for(key).render({})
    config = example_config()
    assert config.operation_name in rendered
    assert config.workspace in rendered
    assert "{{" not in rendered


def test_claude_opus_completeness_passes_at_fifteen_keys() -> None:
    """KOD-63's completeness rule obliges the default set to supply both."""
    assert len(PromptKey) == 15
    members = {path.stem for path in SET_DIR.glob("*.md")}
    assert members == {key.value for key in PromptKey}


# ---------------------------------------------------------------------------
# AC-1 / AC-2 — the templates are legal in-repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PASS_KEYS)
async def test_ported_templates_pass_the_deny_pattern_engine(key: PromptKey) -> None:
    """Zero resolved org-shaped values in repository content."""
    config = AppConfig()
    gate = PatternOutboundContentGate(
        scanners=[
            RegexContentScanner(patterns=config.deny_patterns),
            RegexContentScanner(patterns=ORG_SHAPED_PATTERNS),
        ],
        verdicts=config.deny_pattern_verdicts,
    )
    body = (SET_DIR / f"{key.value}.md").read_text(encoding="utf-8")
    decision = await gate.gate(
        content=body,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.CLEAN, decision.categories


@pytest.mark.parametrize("key", PASS_KEYS)
def test_pass_templates_are_cadence_agnostic(key: PromptKey) -> None:
    """AC-2: no frequency words — scheduling lives in scheduler config."""
    body = (SET_DIR / f"{key.value}.md").read_text(encoding="utf-8").lower()
    for word in CADENCE_WORDS:
        assert word not in body, f"{key.value} contains cadence word {word!r}"


# ---------------------------------------------------------------------------
# AC-7 / AC-8 / D-6 — the traceability artifact
# ---------------------------------------------------------------------------


def test_every_parity_dimension_maps_to_an_existing_template_section() -> None:
    """Each dimension names a template and a section that exists in it."""
    rows = markdown_rows("## Behavior-parity dimensions → template and section")
    assert len(rows) == 6
    for dimension, template, section in rows:
        body = (SET_DIR / f"{template}.md").read_text(encoding="utf-8")
        assert section in body, f"{dimension}: {section} absent from {template}"


def test_all_six_named_dimensions_are_covered() -> None:
    """The six dimensions the fire named are all present."""
    rows = markdown_rows("## Behavior-parity dimensions → template and section")
    dimensions = {row[0] for row in rows}
    assert dimensions == {
        "scan-window checkpointing",
        "atomicity/race guards",
        "bundle-first grouping",
        "queue-state transitions",
        "reply criteria",
        "health mapping",
    }


def template_placeholders() -> set[str]:
    """Every OPERATION-namespace name the shipped templates reference.

    Free: a reference an enclosing ``{{#each}}`` frame supplies is a member of
    the iterated item, not a placeholder resolving to an OperationConfig path.

    Every key, not only the two pass keys: KOD-106's content-audit template
    is the sole reader of ``private_surface``, and a mapping scoped to the
    pass templates would call a field unreachable that a shipped template
    demonstrably reaches.  Per-call variables, set fragments and item-scoped
    member names are removed because none of them resolves to an
    OperationConfig path.
    """
    registry = load_registry()
    referenced: set[str] = set()
    for key in PromptKey:
        referenced |= free_binding_names(registry.template_for(key).body)
    return (
        referenced - SET_FRAGMENT_NAMES - PER_CALL_VARIABLE_NAMES - ITEM_SCOPED_NAMES
    )


def test_placeholder_mapping_is_total_in_both_directions() -> None:
    """Every placeholder maps to one path; every field is template-reachable."""
    rows = markdown_rows("## Placeholder → OperationConfig field")
    mapped = {row[0]: row[1] for row in rows}
    assert len(mapped) == len(rows)

    # Direction 1 — no placeholder the templates reference is unmapped.
    referenced = template_placeholders()
    assert referenced == set(mapped), referenced ^ set(mapped)
    for placeholder, path in mapped.items():
        assert placeholder.split(".")[0] == path

    # Direction 2 — read off the MODEL, never off the table's own rows, so
    # the mapping can no longer be checked against what it was derived from.
    assert set(mapped.values()) == set(OperationConfig.model_fields)


def test_every_operation_config_field_is_reachable_from_a_pass_template() -> None:
    """Direction 2 again, straight from the templates to the model.

    R2 added four fields — principals, agent_identities, repos, initiatives —
    on the reasoning that the passes consume them.  A field no template can
    reach is a field the port did not actually port.
    """
    reachable = {name.split(".")[0] for name in template_placeholders()}
    unreachable = set(OperationConfig.model_fields) - reachable
    assert unreachable == set(), f"no pass template reaches {sorted(unreachable)}"


def test_cutover_document_maps_routine_behavior_to_components() -> None:
    """AC-8: the cutover mapping exists and is non-trivial."""
    rows = markdown_rows("## Routine behavior → kodezart component")
    assert len(rows) >= 6
    text = CUTOVER.read_text(encoding="utf-8")
    assert "Live-workspace resolution" in text
    assert "Cutover execution" in text


def test_example_toml_is_annotated_and_covers_every_field() -> None:
    """D-6: comment-annotated, every field including the four added ones."""
    text = EXAMPLE.read_text(encoding="utf-8")
    assert text.count("#") >= 15
    for field in OperationConfig.model_fields:
        assert field in text
    assert CHECKPOINT_DOCUMENT_KEY in text
    example_config()


def test_readme_points_at_the_operation_config_documents() -> None:
    """D-6/AC-8: both artifacts are discoverable from the README."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/operation.example.toml" in readme
    assert "docs/cutover_mapping.md" in readme
    assert "KODEZART_OPERATION_CONFIG" in readme


def _write_toml(tmp_path: Path, raw: dict[str, object]) -> Path:
    """Serialize a mutated example back to TOML for a failure test."""
    path = tmp_path / "operation.toml"
    path.write_text(_to_toml(raw), encoding="utf-8")
    return path


def _to_toml(raw: dict[str, object]) -> str:
    """Minimal TOML writer covering the shapes the example uses."""
    lines: list[str] = []
    for key, value in raw.items():
        if isinstance(value, str | int | float | bool):
            lines.append(f"{key} = {_scalar(value)}")
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            lines.append(f"{key} = [{', '.join(_scalar(v) for v in value)}]")
    for key, value in raw.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, dict):
                    lines.append(f"\n[{key}.{sub_key}]")
                    lines.extend(f"{k} = {_scalar(v)}" for k, v in sub_value.items())
            scalars = {k: v for k, v in value.items() if not isinstance(v, dict)}
            if scalars:
                lines.append(f"\n[{key}]")
                lines.extend(f"{k} = {_scalar(v)}" for k, v in scalars.items())
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for item in value:
                lines.append(f"\n[[{key}]]")
                lines.extend(
                    f"{k} = {_scalar(v)}"
                    for k, v in item.items()
                    if not _is_table_array(v)
                )
                for sub_key, sub_value in item.items():
                    if not _is_table_array(sub_value):
                        continue
                    sub_items: list[dict[str, object]] = sub_value
                    for sub_item in sub_items:
                        lines.append(f"\n[[{key}.{sub_key}]]")
                        lines.extend(f"{k} = {_scalar(v)}" for k, v in sub_item.items())
    return "\n".join(lines) + "\n"


def write_toml(tmp_path: Path, raw: dict[str, object]) -> Path:
    """Public name for the serialiser, for sibling modules to reuse."""
    return _write_toml(tmp_path, raw)


def _is_table_array(value: object) -> bool:
    """A nested array-of-tables, such as ``repos[].checks``."""
    return isinstance(value, list) and bool(value) and isinstance(value[0], dict)


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return f'"{text}"'
