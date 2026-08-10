"""A pass template may not direct a session at a surface it cannot reach.

The defect this holds shut was live at `9efb5ef`: both pass templates told
the session to read a checkpoint document, re-read item state, move queue
labels, reply to items and append a run-log row — while the sessions
running them were constructed with no MCP attachment at all and, for
preparation, a literally empty tool grant.  The same files' `## What To
Return` sections said "You write nothing yourself" three lines above their
`## Run Log` sections.  Nothing derived a prompt's instruction set from the
capability its pass confers, so no assertion could see it.

**What is derived and what is declared, stated plainly rather than
oversold.**  The premise is derived: `AgentExecutor.stream`'s own parameter
list decides whether any session can attach the vendor MCP, and under
KOD-57's mechanism ruling the vendor MCP is the only access mechanism there
is — never a bespoke client.  The subject set is half derived: `documents`
and `records` are recognised structurally, because their entry models carry
a `system` naming a `DocumentSystem`, which is exactly what makes a value
an address in another system; `knowledge` and `endpoints` are declared
below with their ground, because the model gives them no such marker.

**What it cannot catch.**  A clause naming no configured address at all —
"post a comment on the item" — passes this guard.  The residue is real.
What the guard does close is the whole class of clauses that direct a
session at a NAMED external surface, which is every clause the finding
enumerated, and it closes it for both templates at once rather than for a
list of section names somebody would have to maintain.
"""

import inspect
import re

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.config import AppConfig
from kodezart.core.protocols import AgentExecutor
from kodezart.services.fire_prep_pass import _NO_TOOLS
from kodezart.types.domain.operation import (
    DocumentEntry,
    DocumentSystem,
    RecordDestination,
)
from kodezart.types.domain.prompts import PromptKey

PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)

#: The operation-config namespaces whose values are ADDRESSES in a system
#: outside this process, so an instruction acting on one is an instruction
#: to reach that system.
#:
#: ``documents`` and ``records`` are structural and a test below holds that:
#: their entries carry a ``system``.  ``knowledge`` and ``endpoints`` are
#: named here because the model gives them no marker — a knowledge entry is
#: an id in the knowledge store by construction, and an endpoint is a
#: delivery address by definition.  The other namespaces are excluded on a
#: stated ground rather than by omission: ``queue_states`` and
#: ``workflow_states`` are label vocabulary this process itself writes
#: through ``TrackerPort``, and ``principals``, ``teams``, ``repos``,
#: ``initiatives`` and ``agent_identities`` are things named, not surfaces
#: acted upon.
SURFACE_NAMESPACES: frozenset[str] = frozenset(
    {"documents", "records", "knowledge", "endpoints"},
)

_REFERENCE = re.compile(r"\{\{\s*([a-z_]+)\.")
#: A sentence boundary that a URL cannot forge: a full stop followed by
#: whitespace.  ``https://host.invalid/path`` carries dots followed by
#: letters and is therefore never split in half.
_SENTENCE = re.compile(r"(?<=\.)\s+|\n")
_NEGATION = re.compile(r"\b(no|not|nor|neither|never|nothing|none)\b", re.IGNORECASE)
_THIS_PASS = re.compile(r"\bthis pass\b", re.IGNORECASE)


def registry() -> InRepoPromptRegistry:
    """The shipped registry, unbound: the templates are read, not rendered."""
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set="claude-opus",
        set_overrides={},
        template_overrides={},
        bindings={},
    )


def session_can_attach_mcp() -> bool:
    """Whether the executor port can hand a session the vendor MCP at all."""
    parameters = inspect.signature(AgentExecutor.stream).parameters
    return any("mcp" in name.lower() for name in parameters)


def surfaces_named(sentence: str) -> set[str]:
    """Every unreachable-surface namespace this sentence addresses."""
    return {
        namespace
        for namespace in _REFERENCE.findall(sentence)
        if namespace in SURFACE_NAMESPACES
    }


def disclaims(sentence: str) -> bool:
    """Whether the sentence says what THIS PASS does not do with the surface.

    Both halves are required.  A bare negation passes far too much — "do
    not forget to write the run log" contains one — and a bare "this pass"
    passes the original defect verbatim, since the shipped clause at
    `9efb5ef` read "the lower bound of this pass's scan window".
    """
    return bool(_THIS_PASS.search(sentence) and _NEGATION.search(sentence))


def sentences(key: PromptKey) -> list[str]:
    return [
        part
        for part in _SENTENCE.split(registry().template_for(key).body)
        if part.strip()
    ]


def test_the_premise_this_guard_rests_on_still_holds() -> None:
    """No session can attach the vendor MCP, so no pass may act through it.

    Asserted rather than assumed, and in the direction that fails loudly:
    when the executor port is widened to attach MCP, this reddens, and the
    templates and this guard are then revisited together instead of the
    guard quietly enforcing a boundary that has moved.
    """
    assert not session_can_attach_mcp()


def test_the_preparation_pass_grants_its_session_nothing_at_all() -> None:
    """The second, independent leg for one of the two passes."""
    assert _NO_TOOLS == ()


def test_the_verification_pass_grants_only_tools_bounded_by_its_checkout() -> None:
    """A grant that reaches a checkout is still a grant that reaches no surface."""
    granted = tuple(AppConfig().grooming_pass_allowed_tools)
    assert granted
    assert not [name for name in granted if "mcp" in name.lower()]


def test_the_two_addressed_namespaces_are_recognised_from_the_model() -> None:
    """The structural half of the subject set is genuinely structural."""
    assert DocumentEntry.model_fields["system"].annotation is DocumentSystem
    assert RecordDestination.model_fields["system"].annotation is DocumentSystem
    assert {"documents", "records"} <= SURFACE_NAMESPACES


def test_the_predicate_rejects_the_clause_this_guard_was_written_against() -> None:
    """The shipped instruction at `9efb5ef`, verbatim, must not pass.

    A guard that cannot match the defect it names demonstrates nothing, so
    the defect is stated here in the words it shipped in.
    """
    shipped = (
        "Read the checkpoint document — id {{documents.checkpoint.id}} in the "
        "{{documents.checkpoint.system}} system — and take the recorded marker "
        "as the lower bound of this pass's scan window."
    )
    assert surfaces_named(shipped) == {"documents"}
    assert not disclaims(shipped)


def test_the_predicate_rejects_the_run_log_clause_too() -> None:
    """The second defect the finding named, in the words it shipped in."""
    shipped = (
        "Close the pass by appending exactly one row to the run log — id "
        "{{records.run_log.id}} in the {{records.run_log.system}} system."
    )
    assert surfaces_named(shipped) == {"records"}
    assert not disclaims(shipped)


def test_the_predicate_accepts_a_sentence_that_disclaims_the_act() -> None:
    """The paired positive: the guard is not one that rejects everything."""
    shipped = (
        "The operation's run log is {{records.run_log.id}} in the "
        "{{records.run_log.system}} system, and this pass appends nothing to it."
    )
    assert surfaces_named(shipped) == {"records"}
    assert disclaims(shipped)


def test_no_pass_template_directs_its_session_at_a_surface_it_cannot_reach() -> None:
    """The class, held: every named surface carries its own disclaimer."""
    offending = [
        (key.value, sorted(named), sentence.strip())
        for key in PASS_KEYS
        for sentence in sentences(key)
        if (named := surfaces_named(sentence)) and not disclaims(sentence)
    ]

    assert offending == [], offending


def test_both_pass_templates_still_name_a_surface() -> None:
    """Anti-vacuity: a template naming none would satisfy the guard trivially.

    The surfaces stay named on purpose.  They are the operation's declared
    destinations, and a prompt that simply omitted them would leave a
    session free to imagine its own — which is the defect the addressing
    discipline exists to prevent, arrived at from the other side.
    """
    for key in PASS_KEYS:
        named: set[str] = set()
        for sentence in sentences(key):
            named |= surfaces_named(sentence)
        assert named, key.value
