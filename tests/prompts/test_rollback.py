"""KOD-93-AC-5 — the rollback, executed rather than asserted.

The flip's safety claim is that the legacy corpus is one environment
variable away. A claim of that shape is worth exactly what its test is
worth, so nothing here addresses a set by name the way the golden suites
do: every registry in this module is built from an ``AppConfig`` read under
the rollback environment, through the same composition helper the
application boots with. If the rollback variable stopped reaching the
registry, these tests would be the ones that noticed.

Two halves, both named by the criterion:

1. the effective resolution table logged AT BOOT is 100% legacy across
   every registered function key, and
2. every registered key resolves through the rolled-back set, so the
   corpus a session gets is the one the variable named.

The closing group is this issue's FR-1 ruling made executable. Rolling the
corpus back while leaving the mode at its flipped default leaves an
incoherent pair, because the legacy set declares no draft-critic lens and
the create-only mode's whole contract is that one reviews the draft. That
is why the documented rollback names two variables rather than one, and
why the documents themselves are read here rather than trusted.
"""

import json
import os

import pytest
import structlog

from kodezart.adapters.in_repo_prompt_registry import InRepoPromptRegistry
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import TicketReviewModeError
from kodezart.main import create_app, lifespan
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.prompts.sets import (
    EXAMPLE_OPERATION,
)
from tests.prompts.test_prompt_wiring import DEFAULT_SET, REPO_ROOT

#: The one variable KOD-93-AC-5 names. Written once here so no test in this
#: module can demonstrate the rollback by some other route.
ROLLBACK_ENV = "KODEZART_PROMPT_SET"

#: The second variable a whole-flip rollback needs (FR-1).
MODE_ENV = "KODEZART_TICKET_REVIEW_MODE"


@pytest.fixture
def _rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pristine environment, then the rollback variable and nothing else."""
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(ROLLBACK_ENV, DEFAULT_SET)


async def _boot_registry() -> InRepoPromptRegistry:
    """The production boot path, driven by whatever the environment says."""
    return await boot_prompts(
        config=AppConfig(),
        operation=load_operation_config(EXAMPLE_OPERATION),
        log=structlog.get_logger(__name__),
    )


def _set_toml(set_name: str) -> str:
    """The shipped metadata of *set_name*, read off the tree."""
    path = REPO_ROOT / "src" / "kodezart" / "prompts" / "sets" / set_name / "set.toml"
    return path.read_text(encoding="utf-8")


def _emitted(captured: str) -> list[dict[str, object]]:
    """Parse the JSON log lines the app emitted during boot."""
    events: list[dict[str, object]] = []
    for line in captured.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        events.append(json.loads(stripped))
    return events


# ---------------------------------------------------------------------------
# Half one — the boot resolution table
# ---------------------------------------------------------------------------


def _logged_table(captured: str) -> dict[str, object]:
    """The single resolution-table event, or a failure saying there wasn't one."""
    table_events = [
        e for e in _emitted(captured) if e.get("event") == "prompt_resolution_table"
    ]
    assert len(table_events) == 1
    table = table_events[0]["table"]
    assert isinstance(table, dict)
    return table


@pytest.mark.usefixtures("_rolled_back")
async def test_the_boot_resolution_table_is_wholly_legacy(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """100% legacy across every registered key, read off the boot event.

    Asserted where an operator would read it — the single structured event
    the application logs — rather than re-derived from the registry, since
    the event is what the criterion calls the effective resolution table.
    The mode is rolled back alongside the corpus because that is the
    rollback the documentation gives; the one-variable case is the test
    immediately below, and the two together are the whole fact.
    """
    monkeypatch.setenv(MODE_ENV, TicketReviewMode.REVIEWED.value)

    app = create_app()
    async with lifespan(app):
        pass

    table = _logged_table(capsys.readouterr().out)
    assert set(table) == {key.value for key in PromptKey}
    assert set(table.values()) == {DEFAULT_SET}


@pytest.mark.usefixtures("_rolled_back")
async def test_the_corpus_rolls_back_on_one_variable_and_the_deployment_does_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Measured, and the reason the documented rollback names two variables.

    The prompt corpus IS one variable away, exactly as the criterion says:
    the resolution table this boot logs is 100% legacy across every key.
    The application then refuses to finish starting, because the mode is
    still at its flipped default and the legacy set declares no lens for
    it to dispatch. Both halves are asserted in one test so neither can be
    quoted without the other.
    """
    assert os.environ.get(MODE_ENV) is None, "the one variable speaks alone here"

    app = create_app()
    with pytest.raises(TicketReviewModeError) as excinfo:
        async with lifespan(app):
            pass

    table = _logged_table(capsys.readouterr().out)
    assert set(table) == {key.value for key in PromptKey}
    assert set(table.values()) == {DEFAULT_SET}

    settings = " ".join(excinfo.value.settings)
    assert f"{TicketReviewMode.CREATE_ONLY.value}" in settings
    assert DEFAULT_SET in settings or "no lens at all" in settings


@pytest.mark.usefixtures("_rolled_back")
async def test_every_registered_key_resolves_through_the_rolled_back_set() -> None:
    """The same table from the registry itself, key by key.

    The event above proves what is LOGGED; this proves what is SERVED, so
    a table logged from something other than the registry it describes
    could not pass both.
    """
    table = (await _boot_registry()).resolution_table()

    assert set(table) == set(PromptKey)
    unrolled = sorted(
        key.value for key, source in table.items() if source != DEFAULT_SET
    )
    assert unrolled == [], f"keys that did not roll back: {', '.join(unrolled)}"


# ---------------------------------------------------------------------------
# Half two — every rendered prompt equals its golden
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_rolled_back")
# ---------------------------------------------------------------------------
# FR-1 — the rollback's second variable, pinned as behaviour
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_rolled_back")
def test_rolling_back_the_corpus_alone_leaves_the_flipped_mode_in_place() -> None:
    """The corpus rolls back on one variable; the deployment needs two.

    Recorded rather than smoothed over: the legacy set declares no
    draft-critic lens and cannot acquire one without breaking its own
    byte-freeze, so it cannot satisfy the flipped mode's contract. The
    configuration reports exactly that here, which is why the documented
    rollback names both variables.
    """
    config = AppConfig()

    assert config.prompt_set == DEFAULT_SET
    assert config.ticket_review_mode is TicketReviewMode.CREATE_ONLY


def test_the_documented_rollback_names_both_variables() -> None:
    """The pair above, stated where an operator rolling back would look."""
    documents = {
        name: (REPO_ROOT / name).read_text(encoding="utf-8")
        for name in (".env.example", "README.md")
    }

    for name, document in documents.items():
        assert f"{ROLLBACK_ENV}={DEFAULT_SET}" in document, name
        assert f"{MODE_ENV}={TicketReviewMode.REVIEWED.value}" in document, name


def test_the_legacy_set_declares_no_lens_the_flipped_mode_could_use() -> None:
    """The ground the two-variable rollback rests on, checked in the tree.

    If the legacy set ever declared a draft-critic lens, the coupling
    above would loosen and this test — not someone's memory — is what
    would say so.
    """
    assert "[definitions." not in _set_toml(DEFAULT_SET)


def test_the_flipped_default_set_does_declare_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same fact: the shipped pair IS coherent."""
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)

    default_set = AppConfig().prompt_set
    set_toml = _set_toml(default_set)

    assert "[definitions.draft-critic]" in set_toml


def test_the_documents_name_each_shipped_default_as_the_shipped_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliverable 4's currency check, derived rather than restated.

    The env file's VALUES are already checked against the shipped defaults
    by the documented-surface suite. What that cannot see is prose: a
    paragraph calling the displaced value "the shipped default" reads as
    current and is wrong, which is exactly the drift this flip introduced
    and this test catches. Both claims are derived from AppConfig, so the
    next default to move fails here rather than misleading an operator.
    """
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)
    config = AppConfig()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    displaced = {
        config.prompt_set: DEFAULT_SET,
        config.ticket_review_mode.value: TicketReviewMode.REVIEWED.value,
    }
    for shipped, legacy in displaced.items():
        assert f"`{shipped}` (the shipped default)" in readme or (
            f"the default set (`{shipped}`)" in readme
        ), shipped
        assert f"`{legacy}` (the shipped default)" not in readme, legacy
