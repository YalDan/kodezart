"""KOD-112 fixes 4 and 6: every rendered id names its system, and the
run-log destination is addressable from configuration (AC-41, AC-43).

The criterion's semantic half — "no identifier a reader cannot resolve to a
system" — is operationalized exactly as KOD-112 R4 rules it: every document
and record id emitted by a rendered pass template carries its system token
in the same sentence.  The unresolved-placeholder half is already mechanical
via the fail-loud renderer, and is asserted here on the same rendered text so
both halves of AC-41 are checked against one artifact.
"""

import re
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import OperationConfigError
from kodezart.core.prompt_namespaces import bindings_for, operation_bindings
from kodezart.types.domain.operation import (
    CHECKPOINT_DOCUMENT_KEY,
    DocumentEntry,
    DocumentSystem,
    OperationConfig,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import PER_RUN
from tests.prompts.test_operation_config import write_toml
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"
SRC = REPO_ROOT / "src" / "kodezart"
PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)

# A sentence, for the purpose of "carries its addressable name alongside its
# id": the run of text between full stops.  Nothing numeric is chosen here —
# the boundary is the punctuation the templates are authored with, not a
# window width someone picked.  Newlines are not boundaries: the verbatim
# routine prose wraps a reference across a line break (KOD-60 R20(c)).
_SENTENCE = re.compile(r"[^.]+")


def example_config() -> OperationConfig:
    """The shipped annotated example, loaded and structurally validated."""
    return load_operation_config(EXAMPLE)


def raw_example() -> dict[str, object]:
    """The example config as a plain dict, for mutation in failure tests."""
    return tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))


def rendered_passes() -> dict[PromptKey, str]:
    """Both pass templates rendered from the example operation config."""
    registry = load_registry(bindings=dict(bindings_for(example_config())))
    return {key: registry.template_for(key).render(PER_RUN) for key in PASS_KEYS}


def sentences_containing(text: str, needle: str) -> list[str]:
    """Every sentence of *text* in which *needle* occurs."""
    return [m.group(0) for m in _SENTENCE.finditer(text) if needle in m.group(0)]


# ---------------------------------------------------------------------------
# AC-41 — no unresolved placeholder, and no identifier without its system
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PASS_KEYS)
def test_rendered_pass_carries_no_unresolved_placeholder(key: PromptKey) -> None:
    """AC-41, first half: the fail-loud renderer leaves nothing behind."""
    rendered = rendered_passes()[key]
    assert "{{" not in rendered
    assert "}}" not in rendered


@pytest.mark.parametrize("key", PASS_KEYS)
def test_every_rendered_id_carries_its_configured_name(key: PromptKey) -> None:
    """AC-41, second half: no emitted id is resolvable-to-nothing.

    Every occurrence of every configured document or record id in the
    rendered prompt must sit in a sentence that also carries the entry's
    configured display NAME.  Reshaped under KOD-60 R20(c): the verbatim
    routine prose names the artifact and its vendor beside an id, never the
    config-model enum token — the resolvability a reader needs is the name,
    and the name is config-derived, so the check stays mechanical.  A
    ``knowledge`` entry's value IS its name, so its occurrences satisfy the
    rule by construction and pin the sentence boundary instead.
    """
    config = example_config()
    rendered = rendered_passes()[key]

    addressed: list[tuple[str, str, str]] = [
        (f"documents.{name}", entry.id, entry.name)
        for name, entry in config.documents.items()
        if entry.id is not None
    ]
    addressed += [
        (f"records.{name}", entry.id, entry.name)
        for name, entry in config.records.items()
    ]
    addressed += [
        (f"knowledge.{name}", value, value) for name, value in config.knowledge.items()
    ]

    for path, identifier, display in addressed:
        for sentence in sentences_containing(rendered, identifier):
            assert display in sentence, (
                f"{key.value}: {path} id {identifier!r} is emitted in a sentence "
                f"that never carries its configured name ({display!r}): "
                f"{sentence.strip()!r}"
            )


def test_both_pass_templates_actually_emit_an_addressed_reference() -> None:
    """The configured-name assertion is not vacuously true.

    A test that only checks "every occurrence carries its name" passes
    trivially when there are no occurrences, so the occurrences themselves
    are asserted present — per pass, as each routine actually addresses its
    artifacts (KOD-60 R20(d)): the fire-prep routine reads its own record
    row for the window and writes the run log it names (KOD-245); the
    grooming routine's checkpoint is the initiative status update, and what
    it addresses by id is its own log destination.
    """
    config = example_config()
    run_log = config.records[RunKind.FIRE_PREP.value]
    grooming_log = config.records[RunKind.GROOMING.value]
    rendered = rendered_passes()
    fire = rendered[PromptKey.FIRE_PREP_PASS]
    grooming = rendered[PromptKey.GROOMING_PASS]
    assert run_log.id in fire
    assert run_log.name in fire
    assert grooming_log.id in grooming
    assert grooming_log.name in grooming


def test_a_document_entry_without_a_system_is_unconstructable() -> None:
    """Fix 4 is a real model change, not a rendering convention.

    The fixture is evidence only if the defect it demonstrates could not be
    expressed before: an id-only document entry no longer loads at all.
    """
    with pytest.raises(ValidationError):
        DocumentEntry.model_validate({"id": "an-id-with-no-system"})


def test_the_document_binding_carries_system_and_id_not_a_bare_string() -> None:
    """The render binding changed shape, which is what makes AC-41 mechanical."""
    bindings = operation_bindings(example_config())
    documents = bindings["documents"]
    assert isinstance(documents, dict)
    checkpoint = documents[CHECKPOINT_DOCUMENT_KEY]
    assert checkpoint == {
        "system": DocumentSystem.TRACKER.value,
        "id": example_config().documents[CHECKPOINT_DOCUMENT_KEY].id,
        "id_absent": None,
    }


# ---------------------------------------------------------------------------
# AC-43 — the run-log destination is addressable from configuration
# ---------------------------------------------------------------------------


def test_the_run_log_destination_is_a_config_field() -> None:
    """A write-side registry exists, separate from the read-side one."""
    assert "records" in OperationConfig.model_fields
    destination = example_config().records[RunKind.FIRE_PREP.value]
    assert isinstance(destination, RecordDestination)
    assert destination.system in DocumentSystem
    assert destination.id
    assert destination.append_only is True


def test_a_records_key_outside_the_kind_vocabulary_is_rejected_at_load(
    tmp_path: Path,
) -> None:
    """Record keys ARE the run kinds; a free name is a log nothing writes.

    Absence of a kind is legal (a named absence the recorder reports);
    an unknown key is a typo refused at load, naming the vocabulary
    (KOD-170).
    """
    raw = raw_example()
    records = raw["records"]
    assert isinstance(records, dict)
    records["some_other_record"] = records.pop(RunKind.FIRE_PREP.value)
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(write_toml(tmp_path, raw))
    assert "is not a run kind" in str(excinfo.value)
    assert "some_other_record" in str(excinfo.value)


def test_the_run_log_destination_is_rendered_rather_than_hardcoded() -> None:
    """No shipped source file names a record id of its own.

    A template that speaks of a run log must address its destination
    through a config namespace — any ``records.*`` placeholder, since the
    grooming routine writes to its own log entry (KOD-60 R20(e)), or any
    ``knowledge.*`` placeholder, since the what-lives-where prelude
    addresses the same class of destination from the knowledge registry
    (KOD-84 D-2) — and no shipped file may carry a destination id literal.
    The banned thing is the literal; which registry answers for a
    destination is configuration.
    """
    for destination in example_config().records.values():
        for path in SRC.rglob("*.py"):
            assert destination.id not in path.read_text(encoding="utf-8"), path
        for path in SRC.rglob("*.md"):
            assert destination.id not in path.read_text(encoding="utf-8"), path
    for path in SRC.rglob("*.md"):
        body = path.read_text(encoding="utf-8")
        if "run log" in body.lower():
            assert "{{records." in body or "{{knowledge." in body, path


def test_documents_stays_read_side_with_no_write_flag() -> None:
    """The write capability did not leak back onto the read registry.

    ``name`` is a display title on both registries — what an ensure keys on
    read-side, what a routine addresses its log by write-side (KOD-60 R17)
    — and it is not a write flag. ``append_only`` remains the write
    registry's alone, which is the property this holds. ``container`` is
    creation PLACEMENT, not write capability: it says where boot files the
    document it instates (KOD-166), and grants nothing about writing to it.
    """
    assert set(DocumentEntry.model_fields) == {"system", "name", "id", "container"}
    assert set(RecordDestination.model_fields) == {
        "system",
        "name",
        "id",
        "append_only",
    }
