"""Tests for pure domain functions."""

from kodezart.domain.agent import (
    generate_ralph_branch_name,
    generate_workspace_id,
)


def test_generate_workspace_id_is_hex():
    workspace_id = generate_workspace_id()
    assert len(workspace_id) == 32
    int(workspace_id, 16)


def test_generate_ralph_branch_name_format():
    feature = "kodezart/fix-tests-abc12345"
    ralph = generate_ralph_branch_name(feature)
    assert ralph.startswith(f"{feature}-ralph-")
    suffix = ralph.split("-ralph-")[1]
    assert len(suffix) == 8
    int(suffix, 16)  # validates hex
