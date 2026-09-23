"""The supervision block's structural rows, kept derived from the template.

Each row is an obligation raised against the grooming template by a dry run
of the pass.  A row is resolved by reading the template, never by reading the
block that raised it: one row was already spent by wording the file carried
on its own, and two adopted by effect — the file states the obligation in its
own vocabulary rather than transcribing the block's bytes.

Asserting each row against the file is what keeps the reconciliation a
derivation rather than a transcription.  An edit that re-instates a
merge-gated terminal, drops the composition verdict, or installs a second
scan window beside the base's own fails here instead of passing quietly.
"""

import re

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from tests.prompts.sets import OPUS_SET

GROOMING = default_sets_root() / OPUS_SET / "grooming_pass.md"

#: The base's Scan window sentence, whole: the window runs from the most
#: recent status update of a prior pass and has no upper bound.
SCAN_WINDOW = (
    "- **Mentions & principal comments.** Scan window: issues updated since "
    "the most recent status update posted by a prior grooming pass (when the "
    "discovered initiatives have different timestamps, use the oldest; first "
    "ever pass: 7 days back, once)."
)

#: The base's step-3 sentence that makes the status update's timestamp the
#: next pass's checkpoint, whole.  Sentences split at each sentence end, so
#: the step number ``**3.`` stands apart from it.
STATUS_UPDATE_CHECKPOINT = (
    "Post + report.** One **status update per initiative**, posted every pass "
    "even when nothing changed — this is the traceable per-pass record, the "
    "single routine surface (no separate report comment; the run logs carry "
    "the detail), and its timestamp is the next pass's mention-scan "
    "checkpoint."
)

_SENTENCE_END = re.compile(r"(?<=\.)\s+")


def template() -> str:
    return GROOMING.read_text(encoding="utf-8")


def the_sentence_naming(text: str, words: str) -> str:
    """The one sentence of *text* that carries *words*, read line by line."""
    found = [
        sentence
        for line in text.splitlines()
        for sentence in _SENTENCE_END.split(line)
        if words in sentence
    ]
    assert len(found) == 1, (words, found)
    return found[0]


def test_scan_window_row_is_spent_and_the_files_own_rule_stands_alone():
    """Spent: the base already rules the window, so nothing is written for it.

    The base's Scan window sentence and its step-3 sentence that makes the
    status update's timestamp the next pass's checkpoint are each pinned
    whole, read from the template.  With the base remainder pinned whole
    beside them, a window bound or a marker advance written anywhere in the
    base fails whatever its wording.  The base's own asserts on the same two
    sentences stand beside the pins as the base had them.  No line of the
    template states an upper bound, the adopted sections included, whether
    or not a re-pointed row covers it.
    """
    text = template()
    assert "upper bound" not in text.casefold()
    assert text.count("Scan window:") == 1
    assert (
        "Scan window: issues updated since the most recent status update posted by "
        "a prior grooming pass" in text
    )
    assert "first ever pass: 7 days back, once" in text
    assert "its timestamp is the next pass's mention-scan checkpoint" in text
    assert the_sentence_naming(text, "Scan window:") == SCAN_WINDOW
    assert (
        the_sentence_naming(text, "its timestamp is the next pass's")
        == STATUS_UPDATE_CHECKPOINT
    )


def test_build_verification_reports_the_composition_beside_the_per_ref_verdicts():
    """Half-refuted: grounding stood already; per-ref plus composition is the change."""
    text = template()
    assert "build the **stack head**" in text
    assert "build **every such ref** for a verdict of its own" in text
    assert "build their **composition**" in text
    assert (
        "report the composition's verdict beside the per-ref verdicts, never folded "
        "into one of them" in text
    )
    assert "every unlanded ref principle 2 sends you to" in text


def test_the_queue_terminal_turns_on_demonstration_and_not_on_a_merge():
    """Confirmed in substance: the base did gate its terminal on a merge."""
    text = template()
    assert "PR merged →" not in text
    assert "the work demonstrated in the branch that carries it" in text
    assert (
        "Demonstration remains independent of whether the branch is later merged"
        in text
    )

    assert "never written from a green build or a merged branch" in text
    assert (
        "Only the fresh independent evaluation step may mark criterion leaves finished"
        in text
    )
    assert "queue disposition and cannot establish subtree completion" in text
    assert "workflow_states.done" not in text
