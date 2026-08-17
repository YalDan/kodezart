"""Lane-level verification for PR 5 (KOD-54).

The six integration checks no sub-issue owns alone.
"""

import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.prompts import PromptKey
from tests.prompt_census import PROMPT_FUNCTION_COUNT

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDENS_DIR = REPO_ROOT / "tests" / "prompts" / "goldens"
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"


def default_registry() -> InRepoPromptRegistry:
    """The registry exactly as the composition root builds it."""
    config = AppConfig()
    operation = load_operation_config(EXAMPLE)
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=config.prompt_set,
        set_overrides=config.prompt_set_overrides,
        template_overrides=config.prompt_template_overrides,
        bindings=dict(bindings_for(operation)),
        investigation_cap=config.investigation_cap,
        ticket_review_mode=config.ticket_review_mode,
    )


# ---------------------------------------------------------------------------
# V-1 — registry x pass-template integration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS],
)
def test_pass_templates_resolve_by_key_under_default_configuration(
    key: PromptKey,
) -> None:
    """Both pass templates resolve through the port and render, end to end."""
    registry = default_registry()
    assert registry.resolution_table()[key] == AppConfig().prompt_set
    rendered = registry.template_for(key).render({})
    assert rendered
    assert "{{" not in rendered


def test_claude_opus_completeness_check_passes_at_the_full_census() -> None:
    """Loading succeeds only because the default set supplies every key."""
    registry = default_registry()
    table = registry.resolution_table()
    assert len(table) == PROMPT_FUNCTION_COUNT
    assert set(table) == set(PromptKey)


# ---------------------------------------------------------------------------
# V-2 — no golden was ever re-baselined
# ---------------------------------------------------------------------------


def git(*args: str) -> str:
    """Run git in the repository, returning stdout."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def blob_at(revision: str, relative: str) -> str:
    """The file's bytes at a revision, as text."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{revision}:{relative}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def set_for_golden_dir(directory: Path) -> str:
    """The prompt set whose renders the goldens in *directory* pin.

    Read off the DIRECTORY, never off ``AppConfig().prompt_set``.  A golden
    suite belongs to the set it was rendered from; resolving it from the
    configured default is correct today only because one set exists, and it
    would silently re-point every suite the moment a second one does —
    which is the whole purpose of the set mechanism.

    Suites are named ``<set with underscores>_<variant>``, so the longest
    installed set name the directory begins with is its owner.
    """
    underscored = {
        entry.name.replace("-", "_"): entry.name
        for entry in default_sets_root().iterdir()
        if entry.is_dir()
    }
    matches = [
        name
        for prefix, name in underscored.items()
        if directory.name == prefix or directory.name.startswith(f"{prefix}_")
    ]
    if not matches:
        msg = f"golden suite {directory.name!r} names no installed prompt set"
        raise AssertionError(msg)
    return max(matches, key=len)


def template_for_golden(golden: Path) -> Path:
    """The single set template whose render the golden pins.

    Goldens are named `<key>.txt` or `<key>__<variant>.txt`, under a
    directory naming the set they were rendered from.
    """
    key = golden.stem.split("__", maxsplit=1)[0]
    return default_sets_root() / set_for_golden_dir(golden.parent) / f"{key}.md"


def metadata_for_golden(golden: Path) -> Path:
    """The set metadata the golden's render composes from.

    A rendered member is assembled from TWO authored inputs: its own
    template and its set's metadata — the fragments substituted into it,
    the depth block appended to it, the skills reference and the
    orchestration block bound into it.  A licence read from the template
    alone models half the render, so the metadata of the golden's OWN set
    is the second half.  Still per-set: nothing here lets one set's edit
    license another set's golden.
    """
    return default_sets_root() / set_for_golden_dir(golden.parent) / "set.toml"


def licences_for_golden(golden: Path) -> tuple[Path, ...]:
    """The authored files whose movement licenses this golden's rewrite."""
    return (template_for_golden(golden), metadata_for_golden(golden))


def test_a_golden_suite_resolves_to_the_set_it_was_rendered_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default set is not the answer — the suite's own directory is.

    The environment names a set that does not exist, which is the strongest
    available stand-in for the second set milestone 8 adds: a resolution
    that reads the default configuration cannot survive it, and one that
    reads the directory does not notice.
    """
    monkeypatch.setenv("KODEZART_PROMPT_SET", "a-set-that-does-not-exist")
    assert AppConfig().prompt_set == "a-set-that-does-not-exist"

    for golden in sorted(GOLDENS_DIR.rglob("*.txt")):
        assert template_for_golden(golden).is_file()


def test_a_golden_suite_naming_no_installed_set_fails_loudly() -> None:
    """Silence here would leave a whole suite unguarded by both V-2 checks."""
    with pytest.raises(AssertionError, match="names no installed prompt set"):
        set_for_golden_dir(GOLDENS_DIR / "some_other_engine_empty_skills")


def test_every_golden_names_a_template_that_exists() -> None:
    """The golden-to-template mapping the V-2 checks rest on is total."""
    goldens = sorted(GOLDENS_DIR.rglob("*.txt"))
    assert goldens, "no golden files found"

    unmapped = [
        golden.relative_to(REPO_ROOT).as_posix()
        for golden in goldens
        if not template_for_golden(golden).is_file()
    ]
    assert unmapped == [], f"goldens with no owning template: {unmapped}"


def test_no_golden_diverges_from_its_baseline_unless_its_own_template_did() -> None:
    """V-2, content half: a golden's bytes are its introducing commit's bytes.

    The goldens are the byte-identity evidence for the prompt-set migration.
    A golden that no longer matches the bytes it was introduced with has
    abandoned that evidence, and the only thing that licenses abandoning it
    is the template it renders having genuinely moved since the same commit.
    Editing prompt A while re-baselining golden B is an offender here, because
    the licence is read from B's own sources — its template and its set's
    metadata, the two authored inputs its render composes from — and never
    from another member of the set.
    """
    goldens = sorted(GOLDENS_DIR.rglob("*.txt"))
    assert goldens, "no golden files found"

    offenders: list[str] = []
    for golden in goldens:
        relative = golden.relative_to(REPO_ROOT).as_posix()
        introducing = git("log", "--format=%H", "--", relative).split()[-1]
        if golden.read_text(encoding="utf-8") == blob_at(introducing, relative):
            continue
        licences = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in licences_for_golden(golden)
        ]
        if any(
            blob_at("HEAD", licence) != blob_at(introducing, licence)
            for licence in licences
        ):
            continue
        offenders.append(
            f"{relative}: re-baselined, {' and '.join(licences)} unchanged"
        )
    assert offenders == [], f"goldens re-baselined off their own sources: {offenders}"


def test_every_golden_rewrite_commit_also_changed_that_goldens_template() -> None:
    """V-2, history half: each rewrite rides its own template's rewrite.

    The content check above compares only the endpoints, so a golden could be
    re-baselined in one commit and its template edited in another.  Every
    commit that rewrote a golden must itself have touched the one template
    that golden renders.
    """
    goldens = sorted(GOLDENS_DIR.rglob("*.txt"))
    assert goldens, "no golden files found"

    offenders: list[str] = []
    for golden in goldens:
        relative = golden.relative_to(REPO_ROOT).as_posix()
        licences = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in licences_for_golden(golden)
        ]
        licensing_commits = {
            commit
            for licence in licences
            for commit in git("log", "--format=%H", "--", licence).split()
        }
        rewrites = git("log", "--format=%H", "--", relative).split()[:-1]
        offenders.extend(
            f"{relative}: rewritten at {commit[:8]} with "
            f"{' and '.join(licences)} untouched"
            for commit in rewrites
            if commit not in licensing_commits
        )
    assert offenders == [], f"goldens rewritten off their own sources: {offenders}"


def test_both_golden_suites_exist_and_cover_the_relocated_keys() -> None:
    """V-2: KOD-63's empty-fragment suite and KOD-46's populated suite."""
    empty = {p.stem for p in (GOLDENS_DIR / "claude_opus_empty_skills").glob("*.txt")}
    populated = {
        p.stem for p in (GOLDENS_DIR / "claude_opus_populated_skills").glob("*.txt")
    }
    assert empty
    assert empty == populated


# ---------------------------------------------------------------------------
# V-3 — the sanitization gate covers everything the lane added
# ---------------------------------------------------------------------------


def test_every_writer_in_the_corrected_inventory_names_the_gate() -> None:
    """V-3: the five-writer inventory routes through the gate."""
    workflow = (
        REPO_ROOT / "src" / "kodezart" / "chains" / "ralph_workflow.py"
    ).read_text(encoding="utf-8")
    persister = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "git_change_persister.py"
    ).read_text(encoding="utf-8")

    for writer in (
        "destination=OutboundDestination.BRANCH_NAME",
        "destination=OutboundDestination.ARTIFACT_TICKET_JSON",
        "destination=OutboundDestination.ARTIFACT_CRITERIA_JSON",
        "destination=OutboundDestination.PR_TITLE",
        "destination=OutboundDestination.PR_BODY",
        "destination=OutboundDestination.PR_COMMENT",
    ):
        assert writer in workflow, f"{writer} does not route through the gate"

    assert "OutboundDestination.COMMIT_MESSAGE," in persister
    assert "OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY," in persister

    # KOD-106 deliverable 2: the free-form writer_name strings are GONE, so
    # the event vocabulary and the error's writer field are typed rather
    # than prose. A bare string at a gate call site is the defect.
    assert "writer_name=" not in workflow
    assert "writer_name=" not in persister


# ---------------------------------------------------------------------------
# AC-1 — one rendering path, no substitution in the registry
# ---------------------------------------------------------------------------


def test_the_registry_adapter_performs_no_substitution() -> None:
    """AC-1: exactly one rendering entry point renders every template."""
    registry_source = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "in_repo_prompt_registry.py"
    ).read_text(encoding="utf-8")
    assert "{{" not in registry_source

    renderer_source = (
        REPO_ROOT / "src" / "kodezart" / "core" / "prompt_rendering.py"
    ).read_text(encoding="utf-8")
    assert "def render_template(" in renderer_source
    assert renderer_source.count("def render_template(") == 1


def test_no_second_substitution_implementation_exists_anywhere_in_src() -> None:
    """AC-1, second half: the renderer is unique across the whole package.

    The bullet asks for the absence of ANY second substitution implementation,
    which the single-file check above cannot see.  One definition, in one
    module, renders registry-set templates and ported pass templates alike.
    """
    definitions = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "def render_template(" in path.read_text(encoding="utf-8")
    )
    assert definitions == ["src/kodezart/core/prompt_rendering.py"]
