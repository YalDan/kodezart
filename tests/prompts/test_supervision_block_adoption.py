"""KOD-566 and KOD-567 — the supervision block, adopted section by section.

``docs/supervision-block.md`` is the block's text of record (KOD-872).  Both
the block and the grooming template are read from disk here, so no text is
copied into this module except the re-pointed references, which are the one
place the template is allowed to differ from the block.

* Seven self-contained sections occur in the template byte for byte, heading
  and body, exactly once each.
* Writer Discipline and Supervision Boundaries adopt by effect: every sentence
  of the block's version is carried over in order, and the only sentences that
  differ are the ones whose cross-references name a block section the base
  file does not have.  Those are re-pointed at a base section by its tag, or
  keep the referenced rule's own words inline where the base states it
  nowhere.
* Every adopted section sits between the base's top-level ``<tag>`` sections
  and never inside one, so adopting a section cannot edit a base section;
  the file is never replaced by the block.
"""

import re

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from tests.prompts.sets import OPUS_SET, operation_registry, render_case
from tests.prompts.test_prompt_wiring import REPO_ROOT

BLOCK = REPO_ROOT / "docs" / "supervision-block.md"
TEMPLATE = default_sets_root() / OPUS_SET / "grooming_pass.md"

#: KOD-566's own list of the sections adopted byte for byte.
VERBATIM: tuple[str, ...] = (
    "Supervision Scope",
    "Runtime Verification",
    "Verification Posture",
    "Claim Admissibility",
    "Finding Admissibility",
    "Composition Checks",
    "Supervision Record",
)

#: The two sections KOD-566 adopts by effect.
BY_EFFECT: tuple[str, ...] = ("Writer Discipline", "Supervision Boundaries")

ADOPTED: tuple[str, ...] = VERBATIM + BY_EFFECT

#: block sentence -> adopted sentence, for exactly the sentences whose
#: cross-references name a section of the block the base file does not have
#: (Atomicity Guards, Reply Criteria, Build Verification, Queue State
#: Transitions, Lifecycle States, Health Mapping, Scan Window).  Asserted
#: equal to the set of sentences that differ, so it cannot grow unnoticed.
REPOINTED: dict[str, str] = {
    "Re-read a surface immediately before writing it, per the Atomicity Guards "
    "above, and abandon the write if it moved after this pass's frozen upper "
    "bound.": "Re-read a surface immediately before writing it and abandon the "
    "write if it moved after this pass's upper bound, frozen before reading.",
    "Write each finding as it is formed to the item that owns the surface it "
    "concerns, as a comment under criterion (iii) of the Reply Criteria, "
    "carrying the evidence and the interim reading you will proceed under; the "
    "interim reading is a per-finding reading and is never the pass health "
    "level.": "Write each finding as it is formed to the item that owns the "
    "surface it concerns, as a comment written because the finding changes what "
    "should be done and is not already on the item, carrying the evidence and "
    "the interim reading you will proceed under; the interim reading is a "
    "per-finding reading and is never the per-pass health <health> defines.",
    "Keep no finding in a private surface only: a scratch workspace used for "
    "verification is permitted and its results are reported as scratch results "
    "under Build Verification, and the tracker and the knowledge surfaces this "
    "operation records are the only places a finding survives the pass.": "Keep "
    "no finding in a private surface only: a scratch workspace used for "
    "verification is permitted and its results are reported as scratch "
    "results, never presented as results for the project itself, and the "
    "tracker and the knowledge surfaces this operation records are the only "
    "places a finding survives the pass.",
    "On the strength of a finding you never halt a run, never block a "
    "cross-off and never move a workflow state: a finding is an observation, "
    "and the only transitions this pass performs are the ones Queue State "
    "Transitions and Lifecycle States already rule, on the evidence those "
    "sections require.": "On the strength of a finding you never halt a run, "
    "never block a cross-off and never move a workflow state: a finding is an "
    "observation, and the only transitions this pass performs are the ones the "
    "queue-state machine in <process> already rules, on the evidence it "
    "requires.",
    "Every other write you make is one this prompt already defines — the "
    "replies the Reply Criteria allow, one status update per initiative, and "
    "the single marker advance.": "Every other write you make is one this "
    "prompt already defines — the replies <process> allows, one status update "
    "per initiative, and the single checkpoint advance <process> defines.",
}

_HEADING = re.compile(r"^(?=## )", re.MULTILINE)
_TOP_LEVEL_HEADING = re.compile(r"^## ", re.MULTILINE)
_OPENING_LINE = re.compile(r"^<([a-z_]+)>$", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=\.)\s+")
_TAG_REFERENCE = re.compile(r"<([a-z_]+)>")


def block_text() -> str:
    return BLOCK.read_text(encoding="utf-8")


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def block_sections() -> dict[str, str]:
    """Each ``## `` section of the block: heading line plus body, no separator."""
    chunks = _HEADING.split(block_text())[1:]
    return {
        chunk.split("\n", 1)[0].removeprefix("## "): chunk.rstrip("\n") + "\n"
        for chunk in chunks
    }


def section_bounds(text: str, name: str) -> tuple[int, int]:
    """Where the one ``## name`` section of *text* starts and ends.

    A section runs from its heading line to the blank line that closes it.
    """
    heading = f"## {name}\n"
    starts = [
        match.start()
        for match in re.finditer(f"^{re.escape(heading)}", text, re.MULTILINE)
    ]
    assert len(starts) == 1, f"{name!r} is headed {len(starts)} times"
    start = starts[0]
    end = text.find("\n\n", start)
    return start, len(text) if end == -1 else end + 1


def template_section(text: str, name: str) -> str:
    start, end = section_bounds(text, name)
    return text[start:end]


def sentences(section: str) -> list[str]:
    """The body of *section* (heading dropped), split at each sentence end."""
    body = section.split("\n", 1)[1]
    return [part for part in _SENTENCE_END.split(body.strip()) if part]


def top_level_spans(text: str) -> list[tuple[str, int, int]]:
    """Each top-level ``<tag>…</tag>`` section of *text*: name, start, end.

    A section opens on a line of its own and closes at the first matching
    closing tag after it; an opening line inside an earlier span is nested
    and not top level.
    """
    spans: list[tuple[str, int, int]] = []
    for match in _OPENING_LINE.finditer(text):
        if spans and match.start() < spans[-1][2]:
            continue
        name = match.group(1)
        closing = f"</{name}>"
        close = text.find(closing, match.end())
        assert close != -1, f"<{name}> is never closed"
        spans.append((name, match.start(), close + len(closing)))
    return spans


def test_the_seven_self_contained_sections_occur_byte_identically_once_each():
    """KOD-566: heading and body, byte for byte, exactly once."""
    sections = block_sections()
    text = template_text()
    for name in VERBATIM:
        assert text.count(sections[name]) == 1, name


def test_the_by_effect_sections_carry_every_sentence_and_differ_only_where_repointed():
    """KOD-566: every clause carried over; the table is the whole difference."""
    sections = block_sections()
    text = template_text()
    differing: set[str] = set()
    for name in BY_EFFECT:
        block_sentences = sentences(sections[name])
        adopted = sentences(template_section(text, name))
        differing |= {line for line in block_sentences if line not in adopted}
        assert adopted == [REPOINTED.get(line, line) for line in block_sentences]
    assert differing == set(REPOINTED)


def test_every_tag_a_repointed_reference_names_is_a_top_level_template_section():
    """KOD-566: a re-pointed reference lands on a section the base carries."""
    named = {
        tag for adopted in REPOINTED.values() for tag in _TAG_REFERENCE.findall(adopted)
    }
    carried = {name for name, _, _ in top_level_spans(template_text())}
    assert named
    assert named <= carried


def test_every_adopted_section_sits_between_top_level_sections_never_inside_one():
    """KOD-567: the block is anchored per section, outside every base section."""
    text = template_text()
    spans = top_level_spans(text)
    assert spans
    first_close = spans[0][2]
    for name in ADOPTED:
        start, end = section_bounds(text, name)
        assert start >= first_close, name
        inside = [tag for tag, open_, close in spans if start < close and open_ < end]
        assert inside == [], f"{name!r} sits inside {inside}"


def test_the_template_is_not_the_block_and_holds_no_other_top_level_heading():
    """KOD-567: never a whole-file replacement; only the nine were added."""
    text = template_text()
    assert text != block_text()
    remaining = text
    for name in ADOPTED:
        remaining = remaining.replace(template_section(text, name), "", 1)
    spans = top_level_spans(remaining)
    stray = [
        remaining[match.start() :].split("\n", 1)[0]
        for match in _TOP_LEVEL_HEADING.finditer(remaining)
        if not any(open_ <= match.start() < close for _, open_, close in spans)
    ]
    assert stray == []


def test_the_opus_grooming_prompt_renders_with_every_adopted_section():
    """The adopted text reaches the rendered pass through the registry."""
    text = template_text()
    rendered = render_case(operation_registry(), "grooming_pass")
    for name in ADOPTED:
        assert template_section(text, name) in rendered, name
