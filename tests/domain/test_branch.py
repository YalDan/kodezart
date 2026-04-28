"""Tests for BackupBranchName domain model."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.branch import BackupBranchName


class TestBackupBranchName:
    def test_backup_branch_name_str(self) -> None:
        name = BackupBranchName(source_branch="feat/x", job_id_prefix="abcd1234")
        assert str(name) == "feat/x-backup-abcd1234"

    def test_backup_branch_name_validates_prefix_length(self) -> None:
        with pytest.raises(ValidationError):
            BackupBranchName(source_branch="feat/x", job_id_prefix="short")

    def test_backup_branch_name_validates_source_nonempty(self) -> None:
        with pytest.raises(ValidationError):
            BackupBranchName(source_branch="", job_id_prefix="abcd1234")

    def test_is_backup_true(self) -> None:
        assert BackupBranchName.is_backup("feat-backup-abc12345") is True

    def test_is_backup_false(self) -> None:
        assert BackupBranchName.is_backup("feat/main") is False
