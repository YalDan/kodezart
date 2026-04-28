"""Backup branch naming convention — single source of truth."""

from pydantic import BaseModel, ConfigDict, Field

_BACKUP_INFIX: str = "-backup-"


class BackupBranchName(BaseModel):
    """A backup branch name: ``{source_branch}-backup-{job_id_prefix}``."""

    model_config = ConfigDict(frozen=True)

    source_branch: str = Field(min_length=1)
    job_id_prefix: str = Field(min_length=8, max_length=8)

    def __str__(self) -> str:
        return f"{self.source_branch}{_BACKUP_INFIX}{self.job_id_prefix}"

    @staticmethod
    def is_backup(branch_name: str) -> bool:
        """Check whether *branch_name* is a backup branch."""
        return _BACKUP_INFIX in branch_name
