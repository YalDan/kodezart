"""Construction of the prompt registry and the record of what it resolved.

Moved verbatim from the composition root, which imports and wires rather
than defines.
"""

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.config import AppConfig
from kodezart.core.logging import BoundLogger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.operation import OperationConfig


async def boot_prompts(
    *,
    config: AppConfig,
    operation: OperationConfig | None,
    log: BoundLogger,
) -> InRepoPromptRegistry:
    """Load the registry and record which source answered for each key.

    The resolution table is logged rather than derived later: an override
    that silently failed to apply is otherwise indistinguishable from one
    that was never configured.  A prompt set declaring engines that do not
    include the configured model is a mismatch worth naming, not an error —
    the set still renders.
    """
    prompts = InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=config.prompt_set,
        set_overrides=config.prompt_set_overrides,
        template_overrides=config.prompt_template_overrides,
        bindings=bindings_for(operation),
        investigation_cap=config.investigation_cap,
        fallback_model=config.fallback_model,
    )
    await log.ainfo(
        "prompt_resolution_table",
        table={key.value: source for key, source in prompts.resolution_table().items()},
    )
    declared_engines = prompts.declared_engines()
    if config.model not in declared_engines:
        await log.ainfo(
            "prompt_set_engine_mismatch",
            prompt_set=config.prompt_set,
            declared_engines=list(declared_engines),
            model=config.model,
        )
    return prompts
