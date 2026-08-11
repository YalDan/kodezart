"""Style-regression detectors for the prompt corpus (KOD-83, D-5 and D-6).

One implementation per rule, called from exactly two places: the legacy
self-test, which proves each detector fires against the corpus the rules exist
to retire, and the ``anthropic_v5`` style suite, which requires the same
object to report nothing. A rule that cannot detect the violation it exists to
catch is a vacuous assertion, and sharing the implementation is what makes the
proof transfer from one call site to the other.

Every detector returns the matched snippets rather than a count, so a failure
says what it found and not merely how many.
"""

import re
from collections.abc import Callable, Iterable
from typing import Final

#: A detector reads one template's text and returns what it found.
Detector = Callable[[str], tuple[str, ...]]

_SHERLOCK: Final = re.compile(r"sherlock", re.IGNORECASE)
_WATSON: Final = re.compile(r"watson", re.IGNORECASE)
_SUBAGENT_TYPE: Final = re.compile(r"subagent_type")
_PARALLEL_DISPATCH: Final = re.compile(
    r"Send a SINGLE message containing five parallel Agent tool calls",
)
_NO_DEFER_RULE: Final = re.compile(r"NO-DEFER RULE")
#: The header plus the shaming clauses it introduces. Both are measured on
#: the legacy corpus, so neither half of the union is a guess.
_DEFER_SHAMING: Final = re.compile(
    r"no[- ]defer rule|you are being lazy|is laziness, not scoping|do not defer",
    re.IGNORECASE,
)
_EMPHASIS_INFLATION: Final = re.compile(r"Be extremely thorough")
_ULTRATHINK: Final = re.compile(r"ultrathink", re.IGNORECASE)
_ULTRACODE: Final = re.compile(r"ultracode", re.IGNORECASE)
#: Invocation CONDITIONS, as distinct from a skill's name: the phrasings the
#: legacy corpus uses to tell a session when to reach for one.
_SKILL_INVOCATION: Final = re.compile(
    r"load and apply|load each skill|must load|activity . skill mapping|skill\(s\)",
    re.IGNORECASE,
)
_REASONING_REPRODUCTION: Final = re.compile(
    r"chain of thought|reproduce your reasoning|show your work|think out loud"
    r"|explain your thinking|internal reasoning",
    re.IGNORECASE,
)
#: The interchange boundary rule, matched as a family rather than a literal:
#: the templates say "block" or "blocks" and qualify "data" differently.
_DATA_BOUNDARY: Final = re.compile(
    r"Content inside the tagged blocks? below is data[^.]*never instructions[^.]*\.",
)
_HOUSE_RULES: Final = re.compile(
    r"SOLID DRY KISS|allowed to disable the linter|backwards[- ]compatibility",
    re.IGNORECASE,
)
_ARTIFACT_TAG: Final = re.compile(r"<(/?)([a-z][a-z0-9_]*)>")


def _matches(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in pattern.finditer(text))


def persona_sherlock(text: str) -> tuple[str, ...]:
    """R-1a: the detective persona."""
    return _matches(_SHERLOCK, text)


def persona_watson(text: str) -> tuple[str, ...]:
    """R-1b: the assistant persona the dispatch protocol fans out to."""
    return _matches(_WATSON, text)


def dispatch_subagent_type(text: str) -> tuple[str, ...]:
    """R-2a: the in-prompt subagent selector."""
    return _matches(_SUBAGENT_TYPE, text)


def dispatch_parallel_sentence(text: str) -> tuple[str, ...]:
    """R-2b: the verbatim five-parallel-Agent-calls mandate."""
    return _matches(_PARALLEL_DISPATCH, text)


def no_defer_rule_header(text: str) -> tuple[str, ...]:
    """R-3a: the block header exactly as the pinned census counted it."""
    return _matches(_NO_DEFER_RULE, text)


def defer_shaming(text: str) -> tuple[str, ...]:
    """R-3b: the header and its short variants, including the shaming clauses."""
    return _matches(_DEFER_SHAMING, text)


def emphasis_inflation(text: str) -> tuple[str, ...]:
    """R-4: effort exhortation standing in for a constraint."""
    return _matches(_EMPHASIS_INFLATION, text)


def ultrathink_tokens(text: str) -> tuple[str, ...]:
    """R-5 raw occurrences; placement is checked separately."""
    return _matches(_ULTRATHINK, text)


def ultracode_tokens(text: str) -> tuple[str, ...]:
    """R-6 raw occurrences; containment is checked separately."""
    return _matches(_ULTRACODE, text)


def reasoning_reproduction(text: str) -> tuple[str, ...]:
    """R-8: instructions to emit the model's own reasoning as content."""
    return _matches(_REASONING_REPRODUCTION, text)


def house_rules_prose(text: str) -> tuple[str, ...]:
    """R-11: the standing engineering rules, wherever they are stated."""
    return _matches(_HOUSE_RULES, text)


def data_boundary_sentences(text: str) -> tuple[str, ...]:
    """R-9b: the "data, never instructions" sentence, in any of its forms."""
    return _matches(_DATA_BOUNDARY, text)


def skill_enumeration_detector(skill_names: Iterable[str]) -> Detector:
    """R-7: a detector for *skill_names* plus invocation-condition phrasing.

    The roster is the set's own declared loadouts, so the rule is checked
    against the skills that actually exist rather than a literal list that
    can drift away from them.
    """
    names = sorted({name for name in skill_names if name})
    pattern = (
        re.compile("|".join(re.escape(name) for name in names), re.IGNORECASE)
        if names
        else None
    )

    def detect(text: str) -> tuple[str, ...]:
        named = _matches(pattern, text) if pattern is not None else ()
        return named + _matches(_SKILL_INVOCATION, text)

    return detect


def occurrences_outside(fragment: str, token: Detector, text: str) -> tuple[str, ...]:
    """Tokens found once every rendering of *fragment* is removed from *text*."""
    return token(text.replace(fragment, "")) if fragment else token(text)


def ultrathink_placement_violations(
    text: str,
    *,
    fragment: str,
    utility: bool,
) -> tuple[str, ...]:
    """R-5: exactly one occurrence, as the final block, or none at all.

    A utility template carries no reasoning-depth instruction; every other
    template ends with the fragment verbatim and mentions the token nowhere
    else. Both halves are one rule because either alone permits the failure
    the other catches.
    """
    found = ultrathink_tokens(text)
    if utility:
        return found
    violations: list[str] = []
    if len(found) != 1:
        violations.append(f"{len(found)} occurrences, expected exactly 1")
    if not text.rstrip("\n").endswith(fragment.rstrip("\n")):
        violations.append("does not end with the ultrathink_instruction fragment")
    return tuple(violations)


def unbalanced_artifact_tags(text: str) -> tuple[str, ...]:
    """R-9a: every artifact tag opened is closed, in order.

    An injected artifact whose closing tag is missing runs into whatever
    follows it, which is the boundary the tags exist to draw.
    """
    stack: list[str] = []
    violations: list[str] = []
    for match in _ARTIFACT_TAG.finditer(text):
        closing, name = match.group(1), match.group(2)
        if not closing:
            stack.append(name)
        elif not stack:
            violations.append(f"</{name}> with no opening tag")
        elif stack[-1] != name:
            violations.append(f"</{name}> closes <{stack[-1]}>")
            stack.pop()
        else:
            stack.pop()
    violations.extend(f"<{name}> never closed" for name in stack)
    return tuple(violations)


def artifact_tag_names(text: str) -> tuple[str, ...]:
    """The distinct artifact tags a template opens, in order of appearance."""
    seen: list[str] = []
    for match in _ARTIFACT_TAG.finditer(text):
        if not match.group(1) and match.group(2) not in seen:
            seen.append(match.group(2))
    return tuple(seen)


#: The detectors whose legacy counts are pinned. Keyed by the row name the
#: census table uses, so a failure names the row rather than a bare integer.
COUNTED_DETECTORS: Final[dict[str, Detector]] = {
    "sherlock": persona_sherlock,
    "watson": persona_watson,
    "subagent_type": dispatch_subagent_type,
    "parallel_dispatch_sentence": dispatch_parallel_sentence,
    "no_defer_rule": no_defer_rule_header,
    "be_extremely_thorough": emphasis_inflation,
    "ultrathink": ultrathink_tokens,
    "defer_shaming": defer_shaming,
    "house_rules": house_rules_prose,
}
