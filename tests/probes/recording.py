"""The ledger every live probe records into, and the table it renders as.

One ledger for the whole probe package rather than one per module: a probe
that measures the harness and a probe that measures a rendered set both
produce the same kind of evidence -- a question, the configuration it was
asked under, what was observed, and a verdict read off the observation.
The table is emitted by ``tests/probes/conftest.py`` once per session, so a
probe module carries no reporting machinery of its own.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeRecord:
    """One row of the recorded results table."""

    probe: str
    question: str
    configuration: str
    observed: str
    verdict: str


RECORDS: list[ProbeRecord] = []


def record(
    *,
    probe: str,
    question: str,
    configuration: str,
    observed: str,
    verdict: str,
) -> None:
    RECORDS.append(
        ProbeRecord(
            probe=probe,
            question=question,
            configuration=configuration,
            observed=observed,
            verdict=verdict,
        )
    )


def render_table(records: list[ProbeRecord]) -> str:
    rows = [
        "",
        "live probe ledger -- measured results",
        "",
        "| Probe | Question | Configuration | Observed | Verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        f"| {r.probe} | {r.question} | {r.configuration} | {r.observed} | {r.verdict} |"
        for r in records
    )
    return "\n".join(rows)
