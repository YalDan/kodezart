"""Ref naming conventions and work-ref vocabulary — one source of truth.

This module owns everything that knows the SHAPE of a ref name.  Nothing
else under ``src/`` composes or inspects one, which is what lets the static
check in ``tests/domain/test_base_resolution.py`` assert that no code
derives an issue identity, a role or a parent from a branch name: the
association between an issue and its refs is tracker-side, read through
``TrackerPort``, and a name is a name.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from kodezart.types.base import CamelCaseModel

_BACKUP_INFIX: str = "-backup-"
_INTEGRATION_INFIX: str = "-integration-"


class BackupBranchName(BaseModel):
    """A backup branch name: ``{source_branch}-backup-{workspace_id_prefix}``."""

    model_config = ConfigDict(frozen=True)

    source_branch: str = Field(min_length=1)
    workspace_id_prefix: str = Field(min_length=8, max_length=8)

    def __str__(self) -> str:
        return f"{self.source_branch}{_BACKUP_INFIX}{self.workspace_id_prefix}"

    @staticmethod
    def is_backup(branch_name: str) -> bool:
        """Check whether *branch_name* is a backup branch."""
        return _BACKUP_INFIX in branch_name


class WorkRefRole(StrEnum):
    """What a ref recorded against an issue is FOR.

    One issue carries several refs at once, and the role is the only thing
    that says which is which.  Every member below is already a real ref
    shape in the repository except ``INTEGRATION``, which is a base
    constructed from several blockers' deliverable refs.
    """

    DELIVERABLE = "deliverable"
    ITERATION = "iteration"
    RECOVERY = "recovery"
    BEST_ITERATION = "best_iteration"
    INTEGRATION = "integration"


class WorkRef(CamelCaseModel):
    """One ref an issue carries, at the role it plays.

    ``pushed_head_sha`` is THREE-STATE and is never collapsed to a boolean:
    ``None`` means "not pushed", which is a different fact from "pushed at
    an unknown sha" and from any sha value.  A resolution that read it as
    a boolean would treat an unpushed ref as present.
    """

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    role: WorkRefRole
    branch: str = Field(min_length=1)
    pushed_head_sha: str | None = None
    recorded_at: datetime

    def identity(self) -> tuple[str, WorkRefRole, str, str | None]:
        """What makes this ref THIS ref, ``recorded_at`` excluded.

        The recording instant is assigned by the backend, so two adapters
        storing the same ref may return different ones.  Recording a ref
        that is already recorded is idempotent and is decided here.
        """
        return (self.issue_id, self.role, self.branch, self.pushed_head_sha)


class BaseInput(CamelCaseModel):
    """One resolved blocker contribution to a base, as it was at computation."""

    model_config = ConfigDict(frozen=True)

    blocker_issue_id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    sha: str = Field(min_length=1)


class BaseSpec(CamelCaseModel):
    """The base a lane was dispatched on, plus everything it was computed from.

    ``base_role`` is ``None`` on the trunk arm and ONLY there: trunk is the
    scope's configured branch, not a ref any issue recorded, so no role
    describes it.  It is ``DELIVERABLE`` on the singleton arm and
    ``INTEGRATION`` on the combined arm.

    Two ``BaseSpec`` values compare equal exactly when the base they name
    was computed from the same inputs in the same order.  That equality IS
    the staleness test — see :mod:`kodezart.domain.base_staleness`.
    """

    model_config = ConfigDict(frozen=True)

    inputs: tuple[BaseInput, ...]
    base_branch: str = Field(min_length=1)
    base_role: WorkRefRole | None = None


def trunk_base(branch: str) -> BaseSpec:
    """The base a lane with no blockers has: the trunk it was fired against."""
    return BaseSpec(inputs=(), base_branch=branch, base_role=None)


class IntegrationBranchName(BaseModel):
    """``{issue_id}-integration-{digest}`` — a base constructed from inputs.

    The digest is over the ordered inputs, so a change to ANY input yields a
    different name: an integration ref is rebuilt, never advanced onto new
    inputs, because a base mutating under a graded branch is exactly the
    staleness the arithmetic exists to detect.
    """

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    inputs: tuple[BaseInput, ...]

    def __str__(self) -> str:
        return f"{self.issue_id}{_INTEGRATION_INFIX}{self.digest()}"

    def digest(self) -> str:
        """The full hex digest over the ordered inputs."""
        return _digest_of(self.inputs)


def _digest_of(inputs: Sequence[BaseInput]) -> str:
    joined = "\n".join(
        f"{item.blocker_issue_id}\t{item.branch}\t{item.sha}" for item in inputs
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
