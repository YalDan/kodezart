"""Tests for pure domain functions."""

from kodezart.domain.agent import (
    generate_job_id,
    generate_ralph_branch_name,
)


def test_generate_job_id_is_hex():
    job_id = generate_job_id()
    assert len(job_id) == 32
    int(job_id, 16)


def test_generate_ralph_branch_name_format():
    feature = "kodezart/fix-tests-abc12345"
    ralph = generate_ralph_branch_name(feature)
    assert ralph.startswith(f"{feature}-ralph-")
    suffix = ralph.split("-ralph-")[1]
    assert len(suffix) == 8
    int(suffix, 16)  # validates hex
