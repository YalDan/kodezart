"""In-repo prompt set loader — first ``PromptProvider`` adapter.

A set is a DIRECTORY: ``<sets_root>/<set-name>/set.toml`` plus one
``<function-key>.md`` per member.  Members are data; no Python module ever
lives under the sets tree, so adding a set (complete or partial) is pure
authoring.

Resolution is per function key with strict precedence:

1. an explicit per-step template path,
2. else a per-step set override,
3. else the default set.

The default chain applies only to keys with no override configured.  This
adapter performs NO placeholder substitution — it returns unrendered
``PromptTemplate`` values carrying their set-level fragment bindings.
"""

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Self

from kodezart.core.errors import PromptResolutionError
from kodezart.core.prompt_rendering import (
    PromptTemplate,
    compose_set_member,
    free_binding_names,
)
from kodezart.types.domain.prompts import (
    OrchestrationPrimitive,
    PromptKey,
    PromptSetMetadata,
)
from kodezart.types.domain.subagents import AgentDefinition, SessionPolicy

_METADATA_FILE = "set.toml"
_MEMBER_SUFFIX = ".md"
_DEFINITIONS_DIR = "definitions"
_SKILLS_FRAGMENT = "skills_reference"
_SUPPRESSION_FRAGMENT = "suppression_proxy"
_ORCHESTRATION_SLOT = "orchestration_block"
_INVESTIGATION_SPEC_FRAGMENT = "investigation_spec"
_INVESTIGATION_CAP_NAME = "investigation_cap"
_ULTRACODE_FRAGMENT = "ultracode_instruction"
_TEMPLATE_SOURCE_PREFIX = "template:"


def default_sets_root() -> Path:
    """Filesystem root of the in-repo sets tree."""
    return Path(__file__).resolve().parents[1] / "prompts" / "sets"


def _read_member(path: Path) -> str:
    """Read a member file, dropping its single trailing newline.

    Template files are POSIX text files and end with a newline; that
    newline delimits the file, not the template.
    """
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


class InRepoPromptRegistry:
    """``PromptProvider`` backed by an in-repo directory of template sets."""

    def __init__(
        self,
        *,
        templates: Mapping[PromptKey, PromptTemplate],
        default_metadata: PromptSetMetadata,
        fallback_model: str | None = None,
        definitions: Sequence[AgentDefinition] = (),
    ) -> None:
        self._templates: Mapping[PromptKey, PromptTemplate] = templates
        self._default_metadata: PromptSetMetadata = default_metadata
        self._fallback_model: str | None = fallback_model
        self._definitions: tuple[AgentDefinition, ...] = tuple(definitions)

    @classmethod
    def load(
        cls,
        *,
        sets_root: Path,
        default_set: str,
        set_overrides: Mapping[str, str],
        template_overrides: Mapping[str, str],
        bindings: Mapping[str, object],
        investigation_cap: int,
        fallback_model: str | None = None,
    ) -> Self:
        """Resolve every function key or raise ``PromptResolutionError``."""
        available = _discover_sets(sets_root)
        failures: list[str] = []

        if default_set not in available:
            msg = f"Default prompt set {default_set!r} not found under {sets_root}"
            raise PromptResolutionError(
                msg,
                failing_keys=[k.value for k in PromptKey],
                available_sets=sorted(available),
            )

        metadata = {name: _load_metadata(available[name]) for name in available}
        default_metadata = metadata[default_set]

        _reject_unknown_entries(
            set_overrides=set_overrides,
            template_overrides=template_overrides,
            available_sets=sorted(available),
            failures=failures,
        )
        for key in PromptKey:
            if default_metadata.skill_names(key.value) is None:
                failures.append(key.value)

        templates: dict[PromptKey, PromptTemplate] = {}
        for key in PromptKey:
            resolved = _resolve_key(
                key=key,
                available=available,
                default_set=default_set,
                set_overrides=set_overrides,
                template_overrides=template_overrides,
            )
            if resolved is None:
                if key.value not in failures:
                    failures.append(key.value)
                continue
            body, source, owning_set = resolved
            owning_metadata = metadata.get(owning_set, default_metadata)
            composed = _composed(owning_metadata, key.value, body)
            slotted = _ORCHESTRATION_SLOT in free_binding_names(composed)
            orchestration = (
                _orchestration_block(owning_metadata, investigation_cap)
                if slotted
                else None
            )
            if slotted and orchestration is None:
                if key.value not in failures:
                    failures.append(key.value)
                continue
            templates[key] = PromptTemplate(
                key=key,
                source=source,
                body=composed,
                bindings={
                    **bindings,
                    _SKILLS_FRAGMENT: _skills_fragment(owning_metadata, key),
                    **(
                        {}
                        if orchestration is None
                        else {_ORCHESTRATION_SLOT: orchestration}
                    ),
                },
            )

        if failures:
            msg = "Prompt resolution failed"
            raise PromptResolutionError(
                msg,
                failing_keys=sorted(set(failures)),
                available_sets=sorted(available),
            )

        return cls(
            templates=templates,
            default_metadata=default_metadata,
            fallback_model=fallback_model,
            definitions=_load_definitions(available[default_set], default_metadata),
        )

    def template_for(self, key: PromptKey) -> PromptTemplate:
        """Return the unrendered template registered for *key*."""
        return self._templates[key]

    def resolution_table(self) -> Mapping[PromptKey, str]:
        """Effective ``key -> set/source`` table for the whole registry."""
        return {key: template.source for key, template in self._templates.items()}

    def declared_engines(self) -> Sequence[str]:
        """Engines the default set declares it was authored for."""
        return tuple(self._default_metadata.engines)

    def declared_skills(self, key: PromptKey) -> Sequence[str]:
        """Skill names the default set declares for *key*."""
        return tuple(self._default_metadata.skill_names(key.value) or ())

    def session_policy(self, key: PromptKey) -> SessionPolicy:
        """What *key*'s dispatch declares about its session.

        One object per dispatch rather than four parallel parameters: the
        house rules the set appends, the effort its role runs at, and the
        configured refusal fallback all arrive together, and a set that
        declares no roles produces exactly the policy every dispatch
        expressed before this existed.
        """
        return SessionPolicy(
            system_prompt_append=self._default_metadata.fragments.house_rules,
            effort=self._default_metadata.effort_of(key.value),
            fallback_model=self._fallback_model,
        )

    def definitions(self) -> Sequence[AgentDefinition]:
        """Typed lens definitions the default set declares, name-ordered."""
        return self._definitions

    def system_prompt_append(self) -> str | None:
        """The default set's house rules, delivered as a system-prompt append."""
        return self._default_metadata.fragments.house_rules


def _discover_sets(sets_root: Path) -> dict[str, Path]:
    if not sets_root.is_dir():
        return {}
    return {
        entry.name: entry
        for entry in sorted(sets_root.iterdir())
        if entry.is_dir() and (entry / _METADATA_FILE).is_file()
    }


def _load_metadata(set_dir: Path) -> PromptSetMetadata:
    raw = tomllib.loads((set_dir / _METADATA_FILE).read_text(encoding="utf-8"))
    return PromptSetMetadata.model_validate(raw)


def _reject_unknown_entries(
    *,
    set_overrides: Mapping[str, str],
    template_overrides: Mapping[str, str],
    available_sets: Sequence[str],
    failures: list[str],
) -> None:
    known = {key.value for key in PromptKey}
    for name in (*set_overrides, *template_overrides):
        if name not in known:
            failures.append(name)
    for name, set_name in set_overrides.items():
        if set_name not in available_sets and name not in failures:
            failures.append(name)


def _resolve_key(
    *,
    key: PromptKey,
    available: Mapping[str, Path],
    default_set: str,
    set_overrides: Mapping[str, str],
    template_overrides: Mapping[str, str],
) -> tuple[str, str, str] | None:
    """Return ``(body, source, owning_set)`` or ``None`` when unresolvable."""
    override_path = template_overrides.get(key.value)
    if override_path is not None:
        path = Path(override_path)
        if not path.is_file():
            return None
        return (
            _read_member(path),
            f"{_TEMPLATE_SOURCE_PREFIX}{path}",
            default_set,
        )

    set_name = set_overrides.get(key.value, default_set)
    set_dir = available.get(set_name)
    if set_dir is None:
        return None
    member = set_dir / f"{key.value}{_MEMBER_SUFFIX}"
    if not member.is_file():
        return None
    return (_read_member(member), set_name, set_name)


def _composed(metadata: PromptSetMetadata, name: str, body: str) -> str:
    """One member, assembled from *body* plus the set's fragment content.

    Which fragments a member receives is a property of the SET: the
    suppression proxy where a member asks for it by name, and the
    reasoning-depth block appended to every role outside the declared
    utility roster.  A set that declares neither composes unchanged.
    """
    fragments = metadata.fragments
    substitutions = (
        {_SUPPRESSION_FRAGMENT: fragments.suppression_proxy}
        if fragments.suppression_proxy is not None
        else {}
    )
    appendix = (
        None if name in metadata.utility_keys else fragments.ultrathink_instruction
    )
    return compose_set_member(body, substitutions=substitutions, appendix=appendix)


def _orchestration_block(metadata: PromptSetMetadata, cap: int) -> str | None:
    """The block that fills a member's orchestration slot for this set.

    Which fragment fills the slot is read from the set's declared
    primitive — the value the harness enumeration measured — never judged
    by an engine reading a conditional instruction.  ``None`` means the set
    cannot fill the slot at all: either it declares no primitive, or the
    fragment the primitive selects is absent.  Both are boot failures for
    any member that asks for the slot, which is why the caller reports the
    key rather than rendering an empty block.

    The cap is substituted here rather than at render time because the
    renderer substitutes into a template body, not into a value it has
    just bound: a bound value carrying a tag would reach the session with
    the tag intact.
    """
    fragments = metadata.fragments
    selected = {
        OrchestrationPrimitive.WORKFLOW: fragments.orchestration_workflow,
        OrchestrationPrimitive.AGENT: fragments.orchestration_agents,
    }
    if metadata.orchestration_primitive is None:
        return None
    body = selected[metadata.orchestration_primitive]
    spec = fragments.investigation_spec
    if body is None or spec is None:
        return None
    substitutions = {
        _INVESTIGATION_SPEC_FRAGMENT: compose_set_member(
            spec,
            substitutions={_INVESTIGATION_CAP_NAME: str(cap)},
            appendix=None,
        ),
    }
    if fragments.ultracode_instruction is not None:
        substitutions[_ULTRACODE_FRAGMENT] = fragments.ultracode_instruction
    return compose_set_member(body, substitutions=substitutions, appendix=None)


def _load_definitions(
    set_dir: Path,
    metadata: PromptSetMetadata,
) -> tuple[AgentDefinition, ...]:
    """Build every declared lens from its metadata plus its prompt file."""
    return tuple(
        AgentDefinition(
            name=name,
            description=spec.description,
            prompt=_composed(
                metadata,
                name,
                _read_member(set_dir / _DEFINITIONS_DIR / f"{name}{_MEMBER_SUFFIX}"),
            ),
            tools=tuple(spec.tools),
        )
        for name, spec in sorted(metadata.definitions.items())
    )


def _skills_fragment(metadata: PromptSetMetadata, key: PromptKey) -> str:
    names = metadata.skill_names(key.value) or []
    if not names:
        return ""
    listed = "".join(f"\n- {name}" for name in names)
    return f"{metadata.fragments.skills_reference_header}{listed}\n\n"
