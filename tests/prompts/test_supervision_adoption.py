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

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from tests.prompts.sets import OPUS_SET

GROOMING = default_sets_root() / OPUS_SET / "grooming_pass.md"


def template() -> str:
    return GROOMING.read_text(encoding="utf-8")


def test_scan_window_row_is_spent_and_the_files_own_rule_stands_alone():
    """Spent: the base already rules the window, so nothing is written for it."""
    text = template()
    assert text.count("Scan window:") == 1
    assert (
        "Scan window: issues updated since the most recent status update posted by "
        "a prior grooming pass" in text
    )
    assert "first ever pass: 7 days back, once" in text
    assert "its timestamp is the next pass's mention-scan checkpoint" in text


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
        "Demonstration is a fact about the branch and never about the branch's fate"
        in text
    )
