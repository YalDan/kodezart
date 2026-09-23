"""KOD-566 and KOD-567 — the supervision block, adopted section by section.

``docs/supervision-block.md`` is the block's text of record (KOD-872).  Both
the block and the grooming template are read from disk here, so no text is
copied into this module except the re-pointed references, which are the one
place the template is allowed to differ from the block, the two amended base
lines and the base sentences that concern GitHub, each pinned whole, and the
three prohibitions the amended base rule no longer states.

* Seven self-contained sections occur in the template byte for byte, heading
  and body: each one's whole extent, up to the next heading or top-level
  tag, is the block's section.  The block file itself is pinned by its
  sha256 as the text of record.
* Writer Discipline and Supervision Boundaries adopt by effect: every sentence
  of the block's version is carried over in order, and the only sentences that
  differ are the ones whose cross-references name a block section the base
  file does not have.  Those are re-pointed at a rule the base states, by its
  tag, or keep the referenced rule's own words inline where the base states
  it nowhere; the spent Scan Window row carries nothing, since the base
  window has no upper bound and its status update is the checkpoint.  The
  table also pins the one sentence whose temporal references are restated
  against this pass (KOD-577), and neither section carries a word of the
  cadence list the pass templates are held to.  Its limit: a temporal
  phrase that is on neither the table nor that list is not seen.
* Every adopted section sits between the base's top-level ``<tag>`` sections
  and never inside one, so adopting a section cannot edit a base section;
  the file is never replaced by the block.
* The GitHub boundary has one owner, Supervision Boundaries (KOD-573).  It
  allows verification branches, commits and pushes, so no sentence of the
  template forbids them outright.  Two base lines are amended, and only
  those: the ``<authority>`` GitHub rule points at Supervision Boundaries by
  its heading and adds only what that section does not cover, and the
  closing ``**Boundary:**`` line no longer restates the boundary.  Both are
  pinned whole.  Outside Supervision Boundaries, every sentence that names
  GitHub or an act that section grants (a branch, a commit, a push) is
  either the pointer sentence or one of the base's own sentences that only
  reads, pinned whole, so a new prohibition sentence in any of those terms
  fails.  Its limit: a sentence that forbids a GitHub write without any of
  those words is not seen.
"""

import hashlib
import re

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from tests.prompts.sets import OPUS_SET, operation_registry, render_case
from tests.prompts.test_operation_config import CADENCE_WORDS
from tests.prompts.test_prompt_wiring import REPO_ROOT

BLOCK = REPO_ROOT / "docs" / "supervision-block.md"

#: sha256 of ``docs/supervision-block.md``, the text of record (KOD-872).
BLOCK_SHA256 = "c1778d8d867e3967e8f9f988a7f7a0439b4b792de2836bad954fc398994a8352"
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

#: The by-effect section whose verification branches the base GitHub rule
#: now defers to.
BOUNDARIES = "Supervision Boundaries"

#: The two sections KOD-566 adopts by effect.
BY_EFFECT: tuple[str, ...] = ("Writer Discipline", BOUNDARIES)

ADOPTED: tuple[str, ...] = VERBATIM + BY_EFFECT

#: block sentence -> adopted sentence, for exactly the sentences whose
#: cross-references name a section of the block the base file does not have
#: (Atomicity Guards, Reply Criteria, Build Verification, Queue State
#: Transitions, Lifecycle States, Health Mapping, Scan Window).  Each points
#: only at a rule the base states: the base window has no upper bound and its
#: status update is the checkpoint, so neither a bound nor a marker advance
#: is carried.  It also holds the one sentence whose temporal references are
#: restated against this pass (KOD-577): "this loop", "at once" and "the
#: expected steady state".  Asserted equal to the set of sentences that
#: differ, so it cannot grow unnoticed.
REPOINTED: dict[str, str] = {
    "Re-read a surface immediately before writing it, per the Atomicity Guards "
    "above, and abandon the write if it moved after this pass's frozen upper "
    "bound.": "Re-read a surface immediately before writing it and abandon the "
    "write if the surface changed after this pass read it.",
    "Write each finding as it is formed to the item that owns the surface it "
    "concerns, as a comment under criterion (iii) of the Reply Criteria, "
    "carrying the evidence and the interim reading you will proceed under; the "
    "interim reading is a per-finding reading and is never the pass health "
    "level.": "Write each finding as it is formed to the item that owns the "
    "surface it concerns, as a comment written because the finding changes what "
    "should be done and is not already on the item, carrying the evidence and "
    "the interim reading you will proceed under; the interim reading is a "
    "per-finding reading and is never the health level <health> defines for "
    "this pass.",
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
    "observation, and the only transitions this pass performs are the ones "
    "<authority> and <process> already rule, on the evidence they require.",
    "Every other write you make is one this prompt already defines — the "
    "replies the Reply Criteria allow, one status update per initiative, and "
    "the single marker advance.": "Every other write you make is one this "
    "prompt already defines.",
    "Merging is a human act entirely outside this loop, and several requests "
    "open at once — in parallel or stacked — is the expected steady state "
    "rather than a condition to resolve.": "Merging is a human act entirely "
    "outside this pass, and several requests open during this pass — in "
    "parallel or stacked — are what this pass expects to find rather than a "
    "condition to resolve.",
}

#: The three prohibitions removed from the base GitHub rule, because
#: Supervision Boundaries allows what they forbade (KOD-573).
REMOVED: tuple[str, ...] = (
    "no commits or pushes",
    "no branches",
    "GitHub is read-only for you",
)

#: The one ``<authority>`` rule on GitHub, whole: it points at Supervision
#: Boundaries and adds only what that section does not cover.
AUTHORITY_GITHUB_RULE = (
    "- Write to GitHub beyond what `Supervision Boundaries` allows. All "
    "communication happens in Linear, so no pull-request or issue "
    "comments and no labels. (Reading includes history: a synced issue's "
    "past body revisions via the mirror's edit history are yours to "
    "read.)"
)

#: The closing boundary line, whole: it does not restate the GitHub boundary.
BOUNDARY_LINE = (
    "**Boundary:** you groom to reality and reply in-thread — never an "
    "approval granted or revoked, never a fire, never a moved date, never "
    "a restored edge a principal removed."
)

#: With "GitHub" itself, the acts Supervision Boundaries grants ("cut
#: branches, commit on them, push them"): a sentence naming none of them does
#: not concern a GitHub write.
GITHUB_ACTS: tuple[str, ...] = ("branch", "commit", "push")

#: Every sentence outside Supervision Boundaries that names GitHub or one of
#: its acts, other than the pointer sentence, in template order.  Each reads,
#: describes, or uses the word in another sense; none rules on a GitHub write.
READING: tuple[str, ...] = (
    "- Claude Code cloud job with real git/GitHub, whatever build "
    "toolchains each declared repository's own check chain invokes, `gh`, "
    "and the Linear MCP ({{workspace}} workspace).",
    "- **What you can reach:** the repositories this operation declares "
    "(git/`gh`) and Linear (MCP) — one line each, giving the short name, "
    "the owner/name form you clone, and the branch a lane with no "
    "blockers is based on:\n{{#each repos}}  - `{{this.slug}}` — "
    "{{this.name}}, trunk `{{this.trunk}}`\n{{/each}}  You do NOT have any "
    "other repo, any local file, or any guaranteed skill from another "
    "environment — everything you need is in this prompt; never reference "
    "something you can't open here.",
    "**Ground against the branch the work is on.** Read the real code at "
    "the head of the relevant open PR, not just `main` — the work often "
    "lives unmerged.",
    "Object once, then commit.",
    "Clone every declared repository and run its real chain, capturing "
    "per-check exit codes and HEAD SHAs — on its trunk and on every "
    "unlanded ref principle 2 sends you to, their composition included, "
    "and on gate failure apply principle 1a:\n{{#each repos}}- "
    "{{this.name}} (trunk `{{this.trunk}}`):\n{{#each this.checks}}  - "
    "{{this.name}} — `{{this.command}}`{{#if this.depends_on}}, after "
    "{{this.depends_on}}{{/if}}{{#if this.depends_on_absent}}, a gate: "
    "its failure is a root cause{{/if}}\n{{/each}}{{#if "
    "this.checks_absent}}  - no chain is declared: the repository's own "
    "CI defines its gate — read it in-repo and run that chain, "
    "classifying gates and cascades from what it actually "
    "is\n{{/if}}{{/each}}Also capture each repo's default-branch history "
    "since the mention-scan checkpoint (`git log --format='%h %ad %s' "
    "--stat`) — this commit list is step 2's reconciliation input: every "
    "commit in it must end the pass mapped to an issue or swept against "
    "open-issue premises.",
    "- **Reconcile shipped work** against the verified build + `gh` PR "
    "state (principles 1, 2, 4), grounded in the actual git history, not "
    "just PR lists: for each repo, `git log <default-branch> "
    "--since=<mention-scan checkpoint>` (plus any other branch a fire "
    "landed on), and account for EVERY commit — map each to its merged PR "
    "and that PR to its Linear issue (close/advance the issue with the "
    "SHA as evidence), and treat any commit NOT explained by a reconciled "
    "PR↔issue pair (direct pushes, chore merges with no issue) as an "
    "unmapped change: read its diff, then sweep the open issues whose "
    "premises, frozen-body claims, wrapper fields, or blocking edges "
    "touch the changed paths — an approved fire whose target files just "
    "moved is the highest-value catch.",
    'No commit in the window may end the pass unexplained — "no issue '
    'references it" is the start of the check, not its conclusion.',
    "Work started (branch pushed, fire running) → "
    "{{workflow_states.in_progress}}; open PR → "
    "{{workflow_states.in_review}}; the work demonstrated in the branch "
    "that carries it — that branch's own checks run green at a head SHA "
    "you name — is evidence to verify per branch with `gh`, never "
    "batch-assumed.",
    "A parent's finished state is read from its current criterion subtree "
    "and compliance record, never written from a green build or a merged "
    "branch.",
    "Demonstration remains independent of whether the branch is later "
    "merged, rebased away or superseded.",
    "(iv) *Approved-readiness integrity* on every "
    "`{{queue_states.approved}}` issue — they are one tick from firing: "
    "body present and substantive (an approved issue with an empty or "
    "gutted body is a fire with no prompt — if the issue is "
    "GitHub-synced, recover the pre-wipe body read-only from the mirror's "
    "edit history, `gh api graphql` → `userContentEdits` full snapshots, "
    "restore it verbatim with a provenance comment; otherwise flag it to "
    "the CEO as unfireable), base branch resolvable at `gh`, and "
    "label-vs-body contradictions resolved in the label's favor with a "
    "comment recording the supersession.",
    "When the pass surfaced something a principal should act on now — a "
    "pending decision, a deploy blocker, a principal waiting on a posted "
    "reply — send a push notification leading with that one sentence; it "
    "points at the status update, reply, or issue, never replaces them.",
    "Unmapped commits: a chore merge lands on `main` with no Linear issue "
    '— "nothing references it" is not the end.',
    "The sweep is path-driven, not title-driven: the commit message never "
    "mentioned the fire.",
)

_HEADING = re.compile(r"^(?=## )", re.MULTILINE)
_TOP_LEVEL_HEADING = re.compile(r"^## ", re.MULTILINE)
_OPENING_LINE = re.compile(r"^<([a-z_]+)>$", re.MULTILINE)
_SECTION_END = re.compile(r"^(?:## |</?[a-z_]+>$)", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=\.)\s+")
_TAG_REFERENCE = re.compile(r"<([a-z_]+)>")
_GITHUB_SUBJECT = re.compile(rf"\b(?:GitHub|{'|'.join(GITHUB_ACTS)})", re.IGNORECASE)


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

    A section runs from its heading line to the next ``## `` heading or
    top-level tag line, or to the end of *text*, so a line added anywhere
    before either is inside it.
    """
    heading = f"## {name}\n"
    starts = [
        match.start()
        for match in re.finditer(f"^{re.escape(heading)}", text, re.MULTILINE)
    ]
    assert len(starts) == 1, f"{name!r} is headed {len(starts)} times"
    start = starts[0]
    end = _SECTION_END.search(text, start + len(heading))
    return start, len(text) if end is None else end.start()


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


def test_the_seven_self_contained_sections_are_byte_identical_in_their_extent():
    """KOD-566: each section's whole extent is the block's section, byte for byte.

    The extent runs from the one heading to the next ``## `` heading or
    top-level tag; it equals the block's heading and body followed by the one
    blank line that separates it from what follows.
    """
    sections = block_sections()
    text = template_text()
    for name in VERBATIM:
        assert template_section(text, name) == sections[name] + "\n", name


def test_the_block_file_is_the_text_of_record():
    """KOD-872: the block file is pinned, not only compared with the template.

    ``docs/supervision-block.md`` is the verbatim transcription of snapshot
    comment 5db5205a.  It changes only by a new decision of record, so an edit
    made to it and to the template alike still fails here.
    """
    digest = hashlib.sha256(BLOCK.read_bytes()).hexdigest()
    assert digest == BLOCK_SHA256


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


def test_the_by_effect_sections_carry_no_cadence_word():
    """KOD-577: the authored sections name no cadence; they refer to this pass."""
    text = template_text()
    assert CADENCE_WORDS
    for name in BY_EFFECT:
        section = template_section(text, name).lower()
        carried = [word for word in CADENCE_WORDS if word in section]
        assert carried == [], name


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


def test_the_authority_github_rule_points_at_supervision_boundaries():
    """KOD-573: the amended base rule defers to the adopted section by name."""
    text = template_text()
    authority = [
        text[open_:close]
        for name, open_, close in top_level_spans(text)
        if name == "authority"
    ]
    assert len(authority) == 1
    assert BOUNDARIES in authority[0]
    section_bounds(text, BOUNDARIES)


def test_no_sentence_of_the_template_forbids_commits_pushes_or_branches():
    """KOD-573: no base sentence contradicts what Supervision Boundaries allows."""
    removed = [phrase.casefold() for phrase in REMOVED]
    forbidding = [
        sentence
        for sentence in _SENTENCE_END.split(template_text())
        if any(phrase in sentence.casefold() for phrase in removed)
    ]
    assert forbidding == []


def test_the_github_boundary_lines_are_pinned_whole():
    """KOD-573: the pointer and the closing Boundary line, each exactly."""
    text = template_text()
    authority = [
        text[open_:close]
        for name, open_, close in top_level_spans(text)
        if name == "authority"
    ]
    assert len(authority) == 1
    github = [line for line in authority[0].splitlines() if "GitHub" in line]
    assert github == [AUTHORITY_GITHUB_RULE]
    closing = [line for line in text.splitlines() if line.startswith("**Boundary:**")]
    assert closing == [BOUNDARY_LINE]


def test_the_github_acts_are_the_ones_supervision_boundaries_grants():
    """The walk's vocabulary is read off the section that owns the boundary."""
    boundaries = template_section(template_text(), BOUNDARIES).casefold()
    assert GITHUB_ACTS
    for act in GITHUB_ACTS:
        assert act in boundaries, act


def test_every_github_sentence_outside_supervision_boundaries_only_reads():
    """KOD-573: only Supervision Boundaries rules on a GitHub write.

    Outside it, the one sentence that names a GitHub write is the pointer
    to it; every other sentence naming GitHub or one of its acts is pinned
    whole, so a new or amended one fails whatever its wording.
    """
    text = template_text()
    outside = text.replace(template_section(text, BOUNDARIES), "", 1)
    pointer = _SENTENCE_END.split(AUTHORITY_GITHUB_RULE)[0]
    named = [
        sentence
        for sentence in _SENTENCE_END.split(outside)
        if _GITHUB_SUBJECT.search(sentence)
    ]
    assert named.count(pointer) == 1
    assert [sentence for sentence in named if sentence != pointer] == list(READING)
