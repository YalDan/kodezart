"""Skills configuration, threading, boot pre-flight, and prompt loadouts (KOD-46)."""

from pathlib import Path

import pytest

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError, SkillPreflightError
from kodezart.main import preflight_prompt_skill_loadouts, preflight_skills
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection
from tests.fakes import (
    FakeAgentExecutor,
    FakeAgentRunner,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_criteria,
    make_passing_evaluation,
    make_prompt_provider,
)
from tests.prompts.test_prompt_wiring import GOLDEN_CASES, load_registry

POPULATED = Path(__file__).parent / "goldens" / "claude_opus_populated_skills"
UTILITY_KEYS = (
    PromptKey.BRANCH_NAME,
    PromptKey.TICKET_REVISION,
    PromptKey.COMMIT_MESSAGE,
    PromptKey.PR_DESCRIPTION,
    PromptKey.FIRE_PREP_PASS,
    PromptKey.GROOMING_PASS,
)


class FakeSkillInventory:
    """Host inventory fixture — the names a host provisions at user scope."""

    def __init__(self, names: set[str]) -> None:
        self._names = frozenset(names)

    def available(self) -> frozenset[str]:
        return self._names


# ---------------------------------------------------------------------------
# AC-1b / D-3 — three-state config with no None inhabitant
# ---------------------------------------------------------------------------


def test_shipped_default_is_suppress_all() -> None:
    """The shipped default registers nothing."""
    config = AppConfig()
    assert config.skills_mode is SkillsMode.NONE
    assert config.skills_allowlist == []
    assert config.skills_selection().mode is SkillsMode.NONE


def test_skills_mode_has_no_none_inhabitant() -> None:
    """SkillsMode.NONE is an enum member, not Python ``None``."""
    assert SkillsMode.NONE is not None
    assert set(SkillsMode) == {SkillsMode.NONE, SkillsMode.ALL, SkillsMode.EXPLICIT}


def test_explicit_with_an_empty_allowlist_is_a_typed_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXPLICIT without names is a configuration error, not an empty session."""
    monkeypatch.setenv("KODEZART_SKILLS_MODE", "explicit")
    with pytest.raises(ValueError, match="requires a non-empty"):
        AppConfig.from_env()


@pytest.mark.parametrize("mode", ["none", "all"])
def test_non_explicit_with_an_allowlist_is_a_typed_config_error(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlist that no mode consumes is a configuration error."""
    monkeypatch.setenv("KODEZART_SKILLS_MODE", mode)
    monkeypatch.setenv("KODEZART_SKILLS_ALLOWLIST", '["alpha"]')
    with pytest.raises(ValueError, match="must be empty"):
        AppConfig.from_env()


def test_selection_model_enforces_the_same_two_invariants() -> None:
    """The domain type carries the invariant, not just the settings model."""
    with pytest.raises(ValueError, match="requires a non-empty allowlist"):
        SkillsSelection(mode=SkillsMode.EXPLICIT)
    with pytest.raises(ValueError, match="must not carry an allowlist"):
        SkillsSelection(mode=SkillsMode.NONE, allowlist=("alpha",))


def test_setting_sources_default_to_all_three() -> None:
    """AC-1c: the default keeps every source, including local."""
    assert AppConfig().setting_sources == [
        SettingSource.USER,
        SettingSource.PROJECT,
        SettingSource.LOCAL,
    ]


# ---------------------------------------------------------------------------
# AC-2 / R-5 — boot pre-flight against the host inventory
# ---------------------------------------------------------------------------


def test_preflight_lists_every_unresolvable_name_at_once() -> None:
    """An EXPLICIT allowlist naming absent skills fails loudly, naming all."""
    selection = SkillsSelection(
        mode=SkillsMode.EXPLICIT,
        allowlist=("present", "absent-one", "absent-two"),
    )
    inventory = FakeSkillInventory({"present", "other"})
    with pytest.raises(SkillPreflightError) as excinfo:
        preflight_skills(selection, inventory)
    assert excinfo.value.unresolvable == ("absent-one", "absent-two")
    assert "present" in excinfo.value.available


def test_preflight_passes_for_a_fully_resolvable_allowlist() -> None:
    """A resolvable allowlist boots clean."""
    selection = SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("alpha",))
    preflight_skills(selection, FakeSkillInventory({"alpha", "beta"}))


@pytest.mark.parametrize("mode", [SkillsMode.NONE, SkillsMode.ALL])
def test_preflight_checks_nothing_when_no_name_is_configured(
    mode: SkillsMode,
) -> None:
    """NONE and ALL name no skills, so there is nothing to resolve."""
    preflight_skills(SkillsSelection(mode=mode), FakeSkillInventory(set()))


def test_preflight_accepts_plugin_qualified_names() -> None:
    """Plugin skills are inventoried as ``plugin:skill``."""
    selection = SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("plug:tool",))
    preflight_skills(selection, FakeSkillInventory({"plug:tool"}))


# ---------------------------------------------------------------------------
# AC-3 / D-4 — role -> skill map is data in the set metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("golden_name", sorted(GOLDEN_CASES))
def test_populated_skills_fragment_goldens(golden_name: str) -> None:
    """Body plus POPULATED fragment is pinned independently of the empty goldens."""
    key, variables = GOLDEN_CASES[golden_name]
    rendered = load_registry().template_for(key).render(variables)
    assert rendered == (POPULATED / f"{golden_name}.txt").read_text(encoding="utf-8")


def test_every_rendered_template_names_exactly_its_declared_skills() -> None:
    """AC-3: the reference contains exactly the names the key declares."""
    registry = load_registry()
    for golden_name, (key, variables) in GOLDEN_CASES.items():
        rendered = registry.template_for(key).render(variables)
        declared = registry.declared_skills(key)
        for name in declared:
            assert f"- {name}\n" in rendered, f"{golden_name} omits {name}"
        if not declared:
            assert "SKILLS AVAILABLE FOR THIS ROLE" not in rendered


def test_utility_keys_carry_no_skills_reference() -> None:
    """R-11: the utility keys declare an explicit empty loadout."""
    registry = load_registry()
    for key in UTILITY_KEYS:
        assert registry.declared_skills(key) == ()


def test_non_utility_keys_declare_at_least_one_skill() -> None:
    """AC-3: every non-utility role names the skills serving it."""
    registry = load_registry()
    for key in PromptKey:
        if key in UTILITY_KEYS:
            continue
        assert registry.declared_skills(key)


def test_loadouts_must_be_a_subset_of_the_registered_set() -> None:
    """D-4: a declared name outside the allowlist is a typed boot error."""
    selection = SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("code-review",))
    with pytest.raises(SkillPreflightError) as excinfo:
        preflight_prompt_skill_loadouts(selection, make_prompt_provider())
    assert "security-review" in excinfo.value.unresolvable


def test_loadout_subset_check_passes_when_every_name_is_registered() -> None:
    """The full loadout union boots clean."""
    registry = make_prompt_provider()
    union = sorted(
        {name for key in PromptKey for name in registry.declared_skills(key)}
    )
    selection = SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=tuple(union))
    preflight_prompt_skill_loadouts(selection, registry)


@pytest.mark.parametrize("mode", [SkillsMode.NONE, SkillsMode.ALL])
def test_loadout_subset_check_is_vacuous_without_a_registration_set(
    mode: SkillsMode,
) -> None:
    """Under NONE and ALL nothing is registered by name."""
    preflight_prompt_skill_loadouts(SkillsSelection(mode=mode), make_prompt_provider())


# ---------------------------------------------------------------------------
# AC-1a / D-1 — the selection threads from AppConfig to executor sessions
# ---------------------------------------------------------------------------


async def test_configured_skills_reach_the_executor_through_chain_dispatch() -> None:
    """The parameter threads port -> runner -> service -> every dispatch site."""
    selection = SkillsSelection(mode=SkillsMode.EXPLICIT, allowlist=("code-review",))
    executor = FakeAgentExecutor(events=[])
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        service=service,
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=selection,
    )

    _ = [
        event
        async for event in engine.run(
            prompt="do the thing",
            repo_path="/tmp/fake",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key="k",
        )
    ]

    assert executor.calls
    assert all(call["skills"] == selection for call in executor.calls)


async def test_ralph_loop_threads_the_selection_into_stream_workflow() -> None:
    """The inner loop's execute node forwards the configured selection."""
    selection = SkillsSelection(mode=SkillsMode.ALL)
    runner = FakeAgentRunner(events=[])
    loop = RalphLoop(
        service=runner,
        max_iterations=1,
        plateau_window=2,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=selection,
    )
    with pytest.raises(NoStructuredOutputError):
        _ = [
            event
            async for event in loop.run(
                prompt="p",
                repo_path="/tmp/fake",
                repo_url=None,
                feature_branch="kodezart/f",
                ralph_branch="kodezart/f-ralph",
                base_branch="main",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash"],
                acceptance_criteria=make_criteria("Tests pass"),
                cache_key="k",
                repo_visibility=RepoVisibility.UNKNOWN,
            )
        ]
    assert any(call.get("skills") == selection for call in runner.calls)
