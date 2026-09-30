"""Delivery wire consumers receive one shared PR and an explicit check tri-state."""

from kodezart.types.domain.operation import RepoEntry


def test_forge_exemption_is_declared_and_defaults_false():
    fields = {"url": "https://example.invalid/repository", "trunk": "integration"}
    assert RepoEntry(**fields).forge_exempt is False
    assert RepoEntry(**fields, forge_exempt=True).forge_exempt is True
