"""Configured mandates must reach an actual scheduled Organize owner."""
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.services.test_prompt_passes import _registrations


async def test_configured_mandates_reach_scheduled_owner(tmp_path):
    passes, _ = await _registrations(tmp_path, operation=declared_operation())
    assert "organize" in {entry.name for entry in passes}
