"""One framing for every marked comment that carries a JSON record.

A record written under a marker is read back by the same code that wrote
it, so the framing is stated once: a marker line, then one fenced JSON
object in the model's own canonical spelling. Anything else in that
position is a damaged record and says so.
"""

from pydantic import BaseModel

_FENCE_OPEN = "```json\n"
_FENCE_CLOSE = "\n```"


def render_fenced_record(record: BaseModel) -> str:
    """The record's content beneath its marker: one explicit JSON object."""
    return (
        f"{_FENCE_OPEN}{record.model_dump_json(by_alias=True, indent=2)}{_FENCE_CLOSE}"
    )


def parse_fenced_record[T: BaseModel](*, body: str, marker: str, model: type[T]) -> T:
    """Read the record framed under *marker*, refusing anything else.

    Canonical form is compared rather than merely validated, so a payload
    the writer could not have produced — a repeated key, a reordering, a
    field nothing declares — is refused instead of silently collapsing to
    whichever value the decoder happened to keep.
    """
    prefix = f"{marker}\n{_FENCE_OPEN}"
    if not body.startswith(prefix) or not body.endswith(_FENCE_CLOSE):
        raise ValueError("the record framing is invalid")
    payload = body[len(prefix) : -len(_FENCE_CLOSE)]
    record = model.model_validate_json(payload, strict=True)
    if render_fenced_record(record) != f"{_FENCE_OPEN}{payload}{_FENCE_CLOSE}":
        raise ValueError("the record is not in its canonical form")
    return record
