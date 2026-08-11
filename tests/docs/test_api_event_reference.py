"""``docs/api.md``'s SSE tables are held to the event models that produce them.

The tables were hand-typed and drifted: they documented ``accepted`` on
``workflow_iteration`` after the field had been renamed to ``verdict``, and
omitted two event types entirely.  A reader cannot tell a stale row from a
current one, so the doc has to be derived from the models or checked against
them; it is checked here.

Three facts, each catching a different way the tables rot:

* the documented event set equals the model set — an added or removed event
  type reddens, which is the omission case;
* every field a row names is a real wire field on that event — a renamed or
  deleted field reddens, which is the ``accepted`` case;
* each heading's count equals its rows — the aggregate cannot outlive them.

What is NOT asserted: that a row names every field.  ``Key Fields`` is a
reader's shortlist by design, and demanding completeness would make the
tables unreadable without catching a defect the second fact misses.
"""

import re
from pathlib import Path

from kodezart.types.domain import agent as agent_module
from kodezart.types.domain.agent import AgentEvent

API_DOC = Path(__file__).resolve().parents[2] / "docs" / "api.md"

SECTION = re.compile(r"^### (?P<name>.+?) Events \((?P<count>\d+)\)\s*$")
HEADING = re.compile(r"^#{1,6} ")
ROW = re.compile(r"^\|\s*`(?P<event>[a-z_]+)`\s*\|(?P<fields>.*)\|\s*$")
FIELD = re.compile(r"`(\w+)`")


def event_models() -> dict[str, type[AgentEvent]]:
    """Every concrete event model, keyed by its wire ``type``."""
    models: dict[str, type[AgentEvent]] = {}
    for obj in vars(agent_module).values():
        if not isinstance(obj, type) or not issubclass(obj, AgentEvent):
            continue
        if obj is AgentEvent:
            continue
        models[str(obj.model_fields["type"].default)] = obj
    return models


def wire_fields(model: type[AgentEvent]) -> set[str]:
    """The serialized key of every field on *model* except ``type``."""
    return {
        info.alias or name
        for name, info in model.model_fields.items()
        if name != "type"
    }


def documented_sections() -> list[tuple[str, int, list[tuple[str, list[str]]]]]:
    """``(heading, declared count, rows)`` for each ``### X Events (N)`` table."""
    sections: list[tuple[str, int, list[tuple[str, list[str]]]]] = []
    rows: list[tuple[str, list[str]]] = []
    heading: str | None = None
    declared = 0
    for line in API_DOC.read_text().splitlines():
        match = SECTION.match(line)
        if match is not None:
            if heading is not None:
                sections.append((heading, declared, rows))
            heading, declared, rows = match["name"], int(match["count"]), []
            continue
        if heading is None:
            continue
        if HEADING.match(line):
            sections.append((heading, declared, rows))
            heading, rows = None, []
            continue
        row = ROW.match(line)
        if row is not None:
            rows.append((row["event"], FIELD.findall(row["fields"])))
    if heading is not None:
        sections.append((heading, declared, rows))
    return sections


def documented_events() -> dict[str, list[str]]:
    """Every documented event type mapped to the fields its row names."""
    return {
        event: fields for _, _, rows in documented_sections() for event, fields in rows
    }


def test_the_documented_event_set_is_the_model_set() -> None:
    """A new or deleted event model reddens until the tables follow it."""
    assert set(documented_events()) == set(event_models())


def test_every_field_a_row_names_exists_on_its_event() -> None:
    """A renamed field reddens — this is the ``accepted``/``verdict`` case."""
    models = event_models()
    named = {
        event: sorted(set(fields) - wire_fields(models[event]))
        for event, fields in documented_events().items()
        if event in models
    }
    assert {e: f for e, f in named.items() if f} == {}


def test_each_heading_count_equals_its_rows() -> None:
    """The ``(N)`` in a heading is an aggregate over the table below it."""
    assert {
        heading: (declared, len(rows))
        for heading, declared, rows in documented_sections()
        if declared != len(rows)
    } == {}
