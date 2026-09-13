"""Record the actual criteria prompt/schema/owner disagreement without source edits."""

import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from kodezart.domain.errors import OrganizeWriteRefusalError
from kodezart.types.domain.organize_owner import OrganizeProposal
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.tracker.conftest import CLAIMED_ISSUE


async def main():
    output = {}
    for name in ("claude-opus", "anthropic_v5"):
        path = Path("src/kodezart/prompts/sets") / name / "organize_criteria_author.md"
        data = path.read_bytes()
        output[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "advertised_capabilities": data.decode().split(
                "The current owner can", 1
            )[-1].split("Use the supplied mandate", 1)[0],
        }
    rejected = []
    for capability in ("parent", "blocked_by", "related_to", "priority", "milestone", "split"):
        try:
            OrganizeProposal.model_validate({
                "kind": "unavailable", "issue_id": CLAIMED_ISSUE,
                "capability": capability, "evidence": "Required native graph preparation.",
            })
        except ValidationError as exc:
            rejected.append({"capability": capability, "errors": exc.errors()})
    output["unavailable_rejections"] = rejected
    owner, board, executor = factory()
    original = executor.stream
    observed = []

    async def follow_advertised_body_instruction(**kwargs):
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
                and "Author criterion sub-issue proposals" in kwargs["prompt"]
            ):
                observed.append({
                    "advertises_body_edit": "The current owner can edit the addressed issue's body" in kwargs["prompt"],
                    "body_is_in_schema": "BodyProposal" in kwargs["output_format"]["schema"]["$defs"],
                })
                event = result(structured_output={
                    "kind": "body", "issue_id": CLAIMED_ISSUE,
                    "body": "Prepared source with a concrete criterion requirement.",
                })
            yield event

    executor.stream = follow_advertised_body_instruction
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError as exc:
        output["actual_owner_refusal"] = str(exc)
    output["criteria_calls"] = observed
    output["children_created"] = [
        child.id for child in board.server.issues.values()
        if child.parent_id == CLAIMED_ISSUE
    ]
    print(json.dumps(output, indent=2))


asyncio.run(main())
