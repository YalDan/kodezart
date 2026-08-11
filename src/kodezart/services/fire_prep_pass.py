"""The reinstated fire-prep pass path: the deterministic gates around one pass.

Deliberately MINIMAL.  The pass itself is a judgment surface running as a
full agent session with the tracker attached (the issue's Boundary), so the
session composes and writes; what belongs here is exactly the arithmetic
the criteria name and nothing the withdrawal reverted:

* **the prompt** — composed through the prompt registry from the verbatim
  template plus ``OperationConfig``, failing loudly on any placeholder
  without a config value;
* **the hygiene gate** — KOD-47's scanner entry point run with the quality
  pattern set over a candidate fire body before it is promoted;
* **the check-failure report** — the root-versus-cascade classification
  over the repository's declared chain, so a pass reports one root plus
  its cascades and never a list of independent-looking reds.

No scheduler ownership, no service-owned write path, no session harness.
The composition constructs this service and holds it where the cutover can
invoke it; switching the external routines off is the operator's act.
"""

from collections.abc import Iterable

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import PromptProvider
from kodezart.domain.check_chain import (
    CheckFailureClassification,
    classify_check_failures,
)
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.types.domain.gating import HygieneReport, OutboundDestination
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
)
from kodezart.types.domain.prompts import PromptKey


class FirePrepPass:
    """One preparation pass's deterministic half: prompt, gates, honesty."""

    def __init__(
        self,
        *,
        prompts: PromptProvider,
        scan: HygieneScan,
        operation: OperationConfig,
    ) -> None:
        self._prompts: PromptProvider = prompts
        self._scan: HygieneScan = scan
        self._operation: OperationConfig = operation
        self._log: BoundLogger = get_logger(__name__)

    def compose_prompt(self) -> str:
        """The pass prompt, rendered through the registry from configuration.

        Raises :class:`PromptRenderError` naming every unconditional
        placeholder without a config value — a pass whose identities cannot
        all be resolved refuses to exist rather than running on a hole.
        """
        return self._prompts.template_for(PromptKey.FIRE_PREP_PASS).render({})

    async def gate_body(self, *, body: str) -> HygieneReport:
        """The pre-promotion hygiene verdict for one candidate fire body.

        The body's surface is the tracker: a promoted fire body is written
        onto the issue that carries it.
        """
        return await self._scan.inspect(
            body=body,
            destination=OutboundDestination.TRACKER_COMMENT,
        )

    def report_check_failures(
        self,
        *,
        repo_url: str,
        failed: Iterable[str],
    ) -> CheckFailureClassification:
        """Roots and cascades for one repository's failed check steps.

        Refuses when the operation declares no chain for *repo_url*: with
        no declared dependencies there is nothing to classify against, and
        a flat list of reds is exactly the report the honesty rule bans.
        """
        for repo in self._operation.repos:
            if repo.url == repo_url:
                return classify_check_failures(repo.checks, failed)
        raise OperationMemberAbsentError(
            missing=f"repos entry for {repo_url!r}",
            stops=(
                "check failures cannot be classified into roots and "
                "cascades, so the pass cannot report them honestly"
            ),
        )
