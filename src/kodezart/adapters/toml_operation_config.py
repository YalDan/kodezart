"""Operation-config loader — stdlib tomllib, no new dependency.

Every structural failure the file has is reported in ONE typed error: the
collect-all machinery is built here so the tracker adapter can later add a
live-workspace resolver rather than a redesign.  Existence resolution against
the live workspace is explicitly NOT done here.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import (
    DocumentSystem,
    OperationConfig,
    RunKind,
)

#: Keys a deployment used to declare a scope under a second time, and what to
#: say instead.  A file that still carries one is refused at load: naming the
#: one table is the whole of the migration, and an alias would hide it.
RETIRED_SCOPE_KEYS: Final[Mapping[str, str]] = {
    "audit_scopes": (
        "retired, declare each audited scope once as an [[organize_scopes]] "
        "row carrying its report_issue_key"
    ),
    "supervisor_scopes": (
        "retired, declare each observed scope once as an [[organize_scopes]] row"
    ),
}


#: The one exception to the no-fallback rule, kept so that a v0.2 operation
#: file boots as it is (KOD-903), and limited to what such a file can carry.
#: A v0.2 initiative roster is accepted and has no effect: team scope is the
#: declared boundary now.  Kept apart from RETIRED_SCOPE_KEYS, which means
#: "refuse".
V02_IGNORED_TABLES: Final[tuple[str, ...]] = ("initiatives",)

#: The same exception, for a file that declares no ``[marker_prefixes]`` table
#: at all: each purpose the per-issue path needs takes the marker v0.2 wrote,
#: so the markers v0.2 already left on issues are still read.  v0.2 had no
#: outcome marker, so ``run_outcome`` takes the shipped example's.  A declared
#: table, even an empty one, is taken exactly as written, and every purpose it
#: leaves out still refuses at use.
V02_MARKER_PREFIXES: Final[Mapping[str, str]] = {
    "claim": "kodezart-claim",
    "work_ref": "kodezart-workref",
    "base_spec": "kodezart-basespec",
    "repository": "kodezart-repo",
    "run_outcome": "run-outcome",
}


@dataclass(frozen=True)
class OperationFile:
    """A loaded operation, and what the v0.2 exception did to reach it.

    ``ignored`` names each v0.2 table dropped unread, and ``defaulted`` each
    member supplied because the file left it out: ``marker_prefixes`` when
    the file declares no such table, and ``records.fire`` when its knowledge
    fire log declares neither ``columns`` nor ``outcome_mapping`` and so is
    written in v0.2's title-line shape.  Both are empty for a file that
    declares those members, so the root can say, once, exactly what an old
    file was given.
    """

    config: OperationConfig
    ignored: tuple[str, ...]
    defaulted: tuple[str, ...]


def read_operation_file(path: Path) -> OperationFile:
    """Parse and structurally validate an operation config file.

    Between the parse and the validation, and nowhere else, the v0.2
    exception applies: see :data:`V02_IGNORED_TABLES` and
    :data:`V02_MARKER_PREFIXES`.  Every other absent or unknown member is
    judged by the model exactly as before.
    """
    if not path.is_file():
        msg = f"Operation config not found at {path}"
        raise OperationConfigError(msg, failures=[f"missing file: {path}"])
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        msg = f"Operation config at {path} is not valid TOML"
        raise OperationConfigError(msg, failures=[str(exc)]) from exc
    ignored = tuple(key for key in V02_IGNORED_TABLES if raw.pop(key, None) is not None)
    defaulted: tuple[str, ...] = ()
    if "marker_prefixes" not in raw:
        raw["marker_prefixes"] = dict(V02_MARKER_PREFIXES)
        defaulted = ("marker_prefixes",)
    try:
        config = OperationConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"Operation config at {path} is invalid"
        raise OperationConfigError(msg, failures=_flatten(exc)) from exc
    fire_log = config.records.get(RunKind.FIRE.value)
    if (
        fire_log is not None
        and fire_log.system is DocumentSystem.KNOWLEDGE
        and not fire_log.records_structured(RunKind.FIRE)
    ):
        # The rule fire_record_template reads: this log takes v0.2's title
        # line and its session no record clause. Named, so that is never
        # silent either (KOD-903).
        defaulted = (*defaulted, "records.fire")
    return OperationFile(config=config, ignored=ignored, defaulted=defaulted)


def load_operation_config(path: Path) -> OperationConfig:
    """Parse and structurally validate an operation config file."""
    return read_operation_file(path).config


def _flatten(exc: ValidationError) -> list[str]:
    """One human-readable line per validation failure, all of them.

    A retired scope key gets the migration sentence in place of the generic
    rejection: the file names one scope twice and the reader has to be told
    which table survives.  No sentence above carries a ``;``, which is this
    function's own fragment separator, so each retired key is exactly one
    line and the rest of the file's failures are reported beside it.
    """
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        if error["type"] == "extra_forbidden" and location in RETIRED_SCOPE_KEYS:
            lines.append(f"{location}: {RETIRED_SCOPE_KEYS[location]}")
            continue
        lines.extend(
            f"{location}: {fragment.strip()}"
            for fragment in str(error["msg"]).split(";")
        )
    return lines
