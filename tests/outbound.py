"""Fixed production admission with a scripted judgment I/O dependency."""

from kodezart.adapters.outbound_admission import OutboundAdmission
from kodezart.adapters.reference_content_scanner import ReferenceContentScanner
from kodezart.core.protocols import ContentJudgment
from kodezart.types.domain.gating import (
    OutboundDestination,
    ScanCategory,
    ScanHit,
    ScanResult,
)
from kodezart.types.domain.privacy import PrivateSurface
from tests.fakes import FakeContentJudgment


def make_admission(
    judgment: ContentJudgment | None = None,
    *,
    private_surface: PrivateSurface | None = None,
    fragment_digest: str = "",
) -> OutboundAdmission:
    return OutboundAdmission(
        references=ReferenceContentScanner(
            private_surface=private_surface or PrivateSurface()
        ),
        judgment=judgment if judgment is not None else FakeContentJudgment(),
        fragment_digest=fragment_digest,
    )


class LiteralJudgment:
    """Script a finding at each exact fixture substring, without regex policy."""

    def __init__(self, literals: dict[ScanCategory, list[str]]):
        self.literals = literals
        self.calls: list[str] = []

    async def scan(
        self, *, content: str, destination: OutboundDestination
    ) -> ScanResult:
        self.calls.append(content)
        hits = []
        for category, literals in self.literals.items():
            for literal in literals:
                start = content.find(literal)
                while start >= 0 and literal:
                    hits.append(
                        ScanHit(
                            category=category, start=start, end=start + len(literal)
                        )
                    )
                    start = content.find(literal, start + len(literal))
        return ScanResult(hits=tuple(hits))
