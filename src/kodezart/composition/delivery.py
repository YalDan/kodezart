"""Compose native lane delivery around the already-built fire instance."""

from collections.abc import Sequence

from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    LaneStateWriter,
    OutboundContentGate,
    PromptSetProvider,
)
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.skills import SkillsSelection


def build_native_lane_workflow(
    *,
    fire: RalphWorkflowEngine,
    config: AppConfig,
    service: AgentRunner,
    git: GitService,
    forge: GitHubAPIClient | None,
    prompts: PromptSetProvider,
    skills: SkillsSelection,
    gate: OutboundContentGate,
    repositories: Sequence[RepoEntry],
    # Stated at every composition site rather than defaulted: a forge lane
    # composed without the writer its delivery records through is a refusal
    # at construction, and the choice belongs where the lane is built.
    lane_state: LaneStateWriter | None,
) -> NativeLaneWorkflow:
    """Select one forge capability set and the configured watch/rerun bounds."""
    if fire.criteria is None:
        raise ValueError("Native delivery requires its current criterion reader")
    return NativeLaneWorkflow(
        fire=fire,
        lane_state=lane_state,
        delivery=None
        if forge is None
        else LaneDeliveryCoordinator(
            service=service,
            git=git,
            pr_creator=forge,
            pr_state_reader=forge,
            forge_query=forge,
            ci=forge,
            criteria_reader=fire.criteria,
            prompts=prompts,
            skills=skills,
            gate=gate,
            repositories=repositories,
            git_base_url=config.git.base_url,
            max_concurrent_watches=config.delivery_max_concurrent_watches,
            red_rerun_max_attempts=config.delivery_red_rerun_max_attempts,
        ),
    )
