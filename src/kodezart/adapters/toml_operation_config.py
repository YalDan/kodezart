"""Operation-config loader — stdlib tomllib, no new dependency.

Every structural failure the file has is reported in ONE typed error: the
collect-all machinery is built here so the tracker adapter can later add a
live-workspace resolver rather than a redesign.  Existence resolution against
the live workspace is explicitly NOT done here.
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig

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


def load_operation_config(path: Path) -> OperationConfig:
    """Parse and structurally validate an operation config file."""
    if not path.is_file():
        msg = f"Operation config not found at {path}"
        raise OperationConfigError(msg, failures=[f"missing file: {path}"])
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        msg = f"Operation config at {path} is not valid TOML"
        raise OperationConfigError(msg, failures=[str(exc)]) from exc
    try:
        return OperationConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"Operation config at {path} is invalid"
        raise OperationConfigError(msg, failures=_flatten(exc)) from exc


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
