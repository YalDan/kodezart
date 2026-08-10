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
    RUN_LOG_RECORD_KEY,
    DocumentEntry,
    DocumentSystem,
    OperationConfig,
    RecordDestination,
)
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_operation_config import write_toml
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"
SRC = REPO_ROOT / "src" / "kodezart"
PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)

# A sentence, for the purpose of "carries its system alongside its id": the
# run of text between hard stops.  Nothing numeric is chosen here — the
# boundary is the punctuation the templates are authored with, not a window
# width someone picked.
_SENTENCE = re.compile(r"[^.\n]+")


def example_config() -> OperationConfig:
    """The shipped annotated example, loaded and structurally validated."""
    return load_operation_config(EXAMPLE)


def raw_example() -> dict[str, object]:
    """The example config as a plain dict, for mutation in failure tests."""
    return tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))


def rendered_passes() -> dict[PromptKey, str]:
    """Both pass templates rendered from the example operation config."""
    registry = load_registry(bindings=dict(bindings_for(example_config())))
    return {key: registry.template_for(key).render({}) for key in PASS_KEYS}


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
def test_every_rendered_id_carries_its_system_token(key: PromptKey) -> None:
    """AC-41, second half: no emitted id is resolvable-to-nothing.

    Every occurrence of every configured document or record id in the
    rendered prompt must sit in a sentence that also names the system it
    belongs to.  A reader — human or session — given only this prompt can
    then open the thing it is instructed to read or write.
    """
    config = example_config()
    rendered = rendered_passes()[key]

    addressed: list[tuple[str, str, str]] = [
        (f"documents.{name}", entry.id, entry.system.value)
        for name, entry in config.documents.items()
    ]
    addressed += [
        (f"records.{name}", entry.id, entry.system.value)
        for name, entry in config.records.items()
    ]
    # Every ``knowledge`` value belongs to the knowledge store by
    # construction, so the token a template must name for it is fixed.
    addressed += [
        (f"knowledge.{name}", value, DocumentSystem.KNOWLEDGE.value)
        for name, value in config.knowledge.items()
    ]

    for path, identifier, system in addressed:
        for sentence in sentences_containing(rendered, identifier):
            assert system in sentence, (
                f"{key.value}: {path} id {identifier!r} is emitted in a sentence "
                f"that never names its system ({system!r}): {sentence.strip()!r}"
            )


def test_both_pass_templates_actually_emit_an_addressed_reference() -> None:
    """The system-token assertion is not vacuously true.

    A test that only checks "every occurrence carries its system" passes
    trivially when there are no occurrences, so the occurrences themselves
    are asserted present.
    """
    config = example_config()
    checkpoint = config.documents[CHECKPOINT_DOCUMENT_KEY]
    run_log = config.records[RUN_LOG_RECORD_KEY]
    for key, rendered in rendered_passes().items():
        assert checkpoint.id in rendered, key.value
        assert run_log.id in rendered, key.value


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
    }


# ---------------------------------------------------------------------------
# AC-43 — the run-log destination is addressable from configuration
# ---------------------------------------------------------------------------


def test_the_run_log_destination_is_a_config_field() -> None:
    """A write-side registry exists, separate from the read-side one."""
    assert "records" in OperationConfig.model_fields
    destination = example_config().records[RUN_LOG_RECORD_KEY]
    assert isinstance(destination, RecordDestination)
    assert destination.system in DocumentSystem
    assert destination.id
    assert destination.append_only is True


def test_a_config_without_the_run_log_key_is_rejected_at_load(
    tmp_path: Path,
) -> None:
    """The stable key is required exactly as the checkpoint key is."""
    raw = raw_example()
    records = raw["records"]
    assert isinstance(records, dict)
    records["some_other_record"] = records.pop(RUN_LOG_RECORD_KEY)
    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(write_toml(tmp_path, raw))
    assert RUN_LOG_RECORD_KEY in str(excinfo.value)


def test_the_run_log_destination_is_rendered_rather_than_hardcoded() -> None:
    """No shipped source file names a run-log id or system of its own."""
    destination = example_config().records[RUN_LOG_RECORD_KEY]
    for path in SRC.rglob("*.py"):
        assert destination.id not in path.read_text(encoding="utf-8"), path
    for path in SRC.rglob("*.md"):
        body = path.read_text(encoding="utf-8")
        assert destination.id not in body, path
        if "run log" in body.lower():
            assert "{{records.run_log.id}}" in body, path


def test_documents_stays_read_side_with_no_write_flag() -> None:
    """The write capability did not leak back onto the read registry.

    ``name`` joined the read-side entry when documents became OWNED — it is
    what an ensure keys on — and it is not a write flag: nothing about it
    says a pass records anything there. ``append_only`` remains the write
    registry's alone, which is the property this holds.
    """
    assert set(DocumentEntry.model_fields) == {"system", "name", "id"}
    assert set(RecordDestination.model_fields) == {"system", "id", "append_only"}
