"""Prompt augmentation for ralph loop iteration 2+.

Applies a Sherlock/Watson diagnostic prelude before listing failed criteria
so the fixing agent performs root-cause analysis rather than symptom patching.
"""

from kodezart.types.domain.agent import CriterionResult

_FIX_PRELUDE = """\
You are about to fix criteria that failed in the previous iteration.
DO NOT jump straight to code changes. Follow the diagnostic framework below
so the fix addresses the actual root cause, not just the symptoms.

── WATSON 1: ROOT CAUSE ANALYSIS (subagent_type=Explore) ──
For each failed criterion, investigate WHY it failed.
Trace the data flow through the codebase from input to output.
Identify the exact layer and function where the behaviour diverges from the
expectation. Do not guess — read the relevant source files and test output.

── WATSON 2: FIX ARCHITECTURE (subagent_type=Plan) ──
Design the fix following SOLID DRY KISS hexagonal architecture.
Ensure the change lands in the correct architectural layer.
Do not patch around the problem at a higher layer when the defect lives lower.
Do not introduce wrapper shims or compatibility adapters for your own code.
Do not add code, parameters, or abstractions that are not directly \
required by the fix (YAGNI). If a convention is shared between \
components, enforce it through a typed domain model, not string \
literals.

── WATSON 3: REGRESSION CHECK (subagent_type=Explore) ──
Read the criteria that PASSED in the previous evaluation.
Determine whether the proposed fix will break any of them.
Check for coupling between the code you intend to change and code that is
currently passing. If coupling exists, adjust the fix to preserve correctness.

── WATSON 4: OFFICIAL DOCS (subagent_type=Explore) ──
If the fix involves changes to external API calls, configuration schemas,
or third-party integrations, verify the approach against first-party official
documentation. Do not rely on cached knowledge — read the actual docs.
Do not use deprecated patterns or removed interfaces.

── WATSON 5: ANTI-PATTERN GUARD (subagent_type=Plan) ──
Before writing code, confirm the fix does NOT introduce any of these:
  - A fallback that silently succeeds when the real operation fails.
  - "graceful degradation" that hides a broken code path.
  - A fabricated success signal (e.g. returning a hardcoded OK).
  - Silent error swallowing (bare except, empty catch, ignored return codes).
  - YAGNI additions — code, parameters, or helpers with no direct consumer.
  - Untyped conventions — string literals duplicated across files instead of \
a single typed domain model.
Failure is good — it surfaces bugs. Never disguise it.

── SHERLOCK SYNTHESIS ──
After the Watsons complete their analysis, synthesise findings into a single
coherent fix plan. Resolve any conflicts between Watson recommendations.
Prioritise correctness over speed.

── NO-DEFER RULE ──
Every failed criterion must be addressed in THIS iteration.
Do not defer a fix to a later iteration. Do not mark a criterion as
"will handle next time". If a criterion cannot be fixed without violating
another, raise the conflict explicitly — do not silently skip it.\
"""


def augment_prompt(
    base_prompt: str,
    pending_failures: list[CriterionResult],
) -> str:
    """Augment the base execution prompt with diagnostic prelude and failure list.

    Places the Sherlock/Watson diagnostic framework before the concrete failure
    list so the fixing agent has its analytical instructions before seeing what
    failed.
    """
    failure_lines = "\n".join(
        f"- {failure.criterion}: {failure.reasoning}" for failure in pending_failures
    )
    return (
        f"{base_prompt}\n\n"
        f"{_FIX_PRELUDE}\n\n"
        f"── FAILED CRITERIA FROM PREVIOUS ITERATION ──\n\n{failure_lines}"
    )
