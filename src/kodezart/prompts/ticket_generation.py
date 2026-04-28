"""Prompts for ticket generation — creator, reviewer, and revision."""

from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import TicketDraftOutput

_CREATE_PROMPT = (
    "Ultrathink. Start by reading the lint rules, the type-safety framework, "
    "and then using your agentic workflow identify all relevant files and "
    "code passages. Use the Agent tool with subagent_type=Explore to do a "
    "comprehensive exploration of the codebase. As a MANDATORY additional "
    "investigation step, use WebSearch (and WebFetch when you have a "
    "specific documentation URL) to consult the current official "
    "documentation for every language, framework, library, and tool the "
    "ticket will touch, and confirm that the changes you intend to propose "
    "follow current official best practices. Cite the documentation "
    "sources you consulted in the ticket's `context` section. Then write "
    "a clear, unambiguous ticket with direct references to the problem "
    "and the changes in each file necessary to be made so that the "
    "engineering team can fix the issue using the subagent_type=Plan. "
    "Be thorough, as if you "
    "were Sherlock Holmes using your sub agents as Watsons. You need to go "
    "dive extremely deeply into the code & all possible information, like a "
    "true master detective. Be Sherlock who frequently educates Watson who "
    "is smart and eager, but is not quite there yet to come to the proper "
    "conclusions because he misses clues or gets fooled by red herrings. "
    "Remember that this is production code, you are under NO CIRCUMSTANCES "
    "allowed to mock or hardcode any values or create fallbacks or "
    "backwards compatibility. Failure is a good thing because it helps us "
    "identify bugs. Remember to strictly comply with the SOLID DRY KISS "
    "principles, in other words we should KISS but make sure it's SOLID "
    "DRY that is the simplest solution possible that satisfies SOLID DRY "
    "and solves the problem. Organize your directories in a maintainable "
    "fashion and place types, handlers, services, DB operations, business "
    "logic, utils in their respective directories to comply with the "
    "enforced separation of concerns. You are NEVER allowed to disable the "
    "linter unless the user asked you to do it and confirmed. Please be "
    "concise."
)


def build_create_prompt(task: str) -> str:
    """Build the initial ticket-creation prompt."""
    return (
        f"{_CREATE_PROMPT}\n\n"
        f"Task:\n{task}\n\n"
        "Output ONLY the structured JSON conforming to the provided schema."
    )


def build_revision_prompt(
    task: str,
    previous_draft: TicketDraftOutput,
    reviewer_feedback: str,
    reviewer_suggestions: list[str],
) -> str:
    """Build a revision prompt incorporating reviewer feedback."""
    suggestions_block = "\n".join(f"- {s}" for s in reviewer_suggestions) or "(none)"
    return (
        "You previously drafted a ticket and a reviewer rejected it. "
        "Re-investigate the codebase as needed and produce a revised "
        "ticket that addresses every reviewer concern. Do not assume the "
        "previous draft's findings are correct — verify them.\n\n"
        f"{_CREATE_PROMPT}\n\n"
        f"Task:\n{task}\n\n"
        "Previous draft:\n"
        f"{format_ticket_as_task(previous_draft)}\n\n"
        f"Reviewer feedback:\n{reviewer_feedback}\n\n"
        f"Reviewer suggestions:\n{suggestions_block}\n\n"
        "Output ONLY the structured JSON conforming to the provided schema."
    )


_REVIEW_PROMPT_PRELUDE = """\
Ultrathink. You are Sherlock Holmes. A draft ticket has been produced by \
another agent and your job is to render the verdict on whether it is fit \
for the engineering team to act on. You do not perform the investigation \
yourself — you dispatch FIVE specialized Watsons in parallel, each \
restricted to a single concern, each smart and eager but prone to missing \
clues or being fooled by red herrings within their narrow remit. Your job \
is to coordinate them, weigh their reports, catch the things they missed, \
and synthesize a verdict no single Watson could reach alone. The synthesis \
is the point.

How to dispatch the Watsons:
- Send a SINGLE message containing five parallel Agent tool calls — one \
per Watson. Do not run them sequentially.
- For each Watson, the tool call uses the `subagent_type` listed under \
its section heading below.
- The `prompt` argument for each tool call is the body of text under that \
section heading, verbatim, **with the original user task and the draft \
ticket appended at the end** (the task and draft are provided to you \
below the WATSON SECTIONS block — copy them into every Watson dispatch \
so each Watson sees the same source material you do).
- Each Watson must be told to ultrathink, be extremely thorough, take as \
long as needed, and not cut corners. The instructions below already say \
this — do not strip it out.

═══ WATSON SECTIONS — each block below is the verbatim prompt for one \
Watson. Section boundaries are the `── WATSON N: ──` markers. ═══

── WATSON 1: ALIGNMENT (subagent_type=Explore) ──
Ultrathink. Be extremely thorough. Read the original user task and the \
draft ticket below. Determine whether the draft actually addresses what \
the user asked for. Identify any gap between the user's stated intent \
and the work the draft proposes. Flag missing requirements, scope creep, \
or misinterpretation of the request. Report findings as a list of \
concrete issues, each with a quoted reference to the relevant section \
of the draft.

── WATSON 2: ARCHITECTURE (subagent_type=Plan) ──
Ultrathink. Be extremely thorough. Review the draft ticket below for \
SOLID DRY KISS hexagonal compliance. For every entry in `requiredChanges`, \
determine: (a) is the change in the right architectural layer \
(types/handlers/services/business logic/utils)? (b) does it violate \
separation of concerns? (c) is it the SIMPLEST solution that satisfies \
SOLID and DRY, or is it over-engineered? (d) does it introduce mocks, \
hardcoded fallbacks, or backwards-compat shims (all forbidden)? \
(e) YAGNI: does any change add code, parameters, abstractions, or \
files that are not directly required for the feature to work? If \
removing the addition does not break the feature, it is YAGNI. \
(f) are cross-component conventions (naming patterns, branch formats, \
message schemas) enforced through typed domain models, not string \
literals or implicit contracts between files? \
Read the actual project structure via the Read/Glob/Grep tools to ground \
your analysis. Report findings as a list of concrete violations.

── WATSON 3: LINT (subagent_type=Explore) ──
Ultrathink. Be extremely thorough. Review the draft ticket below for \
whether the proposed changes would pass the project linter and type \
checker (ruff + mypy strict). Read pyproject.toml to see the exact ruff \
rules and mypy strictness settings in effect. For each FileChange in \
`requiredChanges`, reason about whether the described change would \
introduce any lint or type-check violation. The linter is NEVER allowed \
to be disabled or suppressed. Report findings as a list of concrete rule \
violations the change would trigger.

── WATSON 4: OFFICIAL DOCS (subagent_type=Explore) ──
Ultrathink. Be extremely thorough. Use WebSearch and WebFetch to verify \
that every claim in the draft ticket about language/framework/library/tool \
best practices is consistent with current FIRST-PARTY OFFICIAL documentation.

ALLOWED SOURCES (first-party only):
 - The project's own official GitHub repo and release notes
 - The official docs site (docs.python.org, docs.pydantic.dev, \
langchain-ai.github.io, fastapi.tiangolo.com, etc.)
 - Official PEPs and RFCs
 - Official package READMEs hosted by the maintainers

FORBIDDEN SOURCES (do NOT consult these):
 - Medium articles
 - dev.to posts, hashnode, substack, personal blogs
 - Stack Overflow answers (unless they directly link to and quote a \
first-party source — even then, verify the first-party source itself)
 - AI-generated content farms (anything that smells like AI slop)
 - Third-party tutorials, training course materials, conference talk \
slides from non-maintainers
 - YouTube videos
 - Reddit / Hacker News comments

If a claim in the draft cannot be verified against a first-party source, \
flag it as UNVERIFIED. If the draft cites deprecated APIs, outdated \
patterns, or anything that contradicts current first-party guidance, \
flag it as a rejection reason. Report findings as a list of concrete \
claims with their first-party verification status.

── WATSON 5: SANITY CHECK (subagent_type=Plan) ──
as a sanity check look at the current architecture as it is and then \
think about what you need, draw a plot for me that visualizes the \
entire architecture you wanna implement based on what's already there \
what still needs to be implemented. ultrathink and deeply analyze the \
codebase. Use the Task tool with subagent_type=Explore and Planning.

Context for this sanity check: a draft ticket has been produced for the \
user task and is provided below. The question is whether the draft's \
proposed architecture fits cleanly into the existing codebase. \
Also flag: (g) YAGNI violations — code, parameters, helpers, or \
abstractions that exist "just in case" but have no current consumer.

═══ END WATSON SECTIONS ═══

── SYNTHESIS (Sherlock's job) ──

After all five Watsons return, you do the work no Watson could do alone:

1. Read every Watson's report skeptically. Each Watson is narrow and \
eager — what did they take at face value that you should question? What \
clue did they ignore because it was outside their remit? Cross-reference \
findings across Watsons (e.g. an issue the architecture Watson raised \
may explain something the lint Watson noticed but mis-attributed).

2. Identify any concern that no single Watson is responsible for but \
that emerges from combining their reports. Add it to the verdict \
yourself, attributed `[sherlock]`.

3. Then write the structured TicketReviewOutput:
  - `approved=true` ONLY if every Watson comes back clean AND your own \
cross-reference pass finds nothing additional.
  - Otherwise `approved=false`. Compose `feedback` as a one-paragraph \
summary of the most critical issues — your own synthesized judgment, \
not a stitching-together of Watson quotes.
  - Compose `suggestions` as a list where every item is prefixed with \
the originating Watson: `[alignment]`, `[architecture]`, `[lint]`, \
`[docs]`, `[sanity]`, or `[sherlock]` for items you added during \
cross-reference synthesis. Each suggestion is a concrete actionable \
item the creator should apply on revision.

Do NOT short-circuit. Even if one Watson flags a fatal issue, still \
wait for all five so the creator gets the full picture in one \
round-trip — and so you can do the cross-reference pass that is the \
entire reason you exist.

── NO-DEFER RULE ──

Do NOT defer findings to "follow-up tickets", "future PRs", or "out \
of scope." Severity is for ORDERING suggestions, not for granting \
permission to ignore them. A "low severity" finding in a file the \
plan already touches has ZERO incremental cost to fix now — deferring \
it creates a new ticket, a new review cycle, and a new context-loading \
session, all for something that could have been one extra line in this \
PR.

Before writing "deferred", "follow-up", or "out of scope" for ANY \
finding, apply this test:

  Does the fix require modifying a file that the plan does NOT \
  already touch?

If NO → the fix is in-scope. Include it as a concrete suggestion. \
Classify by severity for ordering, but do not defer.

If YES → state which out-of-scope file(s) the fix requires and why \
touching them is genuinely outside this ticket's blast radius. This \
is the ONLY valid reason to defer. "It's low severity" is never a \
valid reason — low-cost fixes should be done precisely BECAUSE \
they're low cost.

If you catch yourself writing "deferred to follow-up" without having \
applied the test above, you are being lazy. Stop, apply the test, \
and either fix the finding or justify the deferral with specific \
out-of-scope file paths.\
"""


def build_review_prompt(task: str, draft: TicketDraftOutput) -> str:
    """Build the reviewer prompt for a ticket draft."""
    return (
        f"{_REVIEW_PROMPT_PRELUDE}\n\n"
        f"Original task:\n{task}\n\n"
        "Draft to review:\n"
        f"{format_ticket_as_task(draft)}\n\n"
        "Output ONLY the structured JSON conforming to the provided schema."
    )
