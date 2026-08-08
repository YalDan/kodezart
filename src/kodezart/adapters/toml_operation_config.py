"""Operation-config loader — stdlib tomllib, no new dependency.

Every structural failure the file has is reported in ONE typed error: the
collect-all machinery is built here so the tracker adapter can later add a
live-workspace resolver rather than a redesign.  Existence resolution against
the live workspace is explicitly NOT done here.
"""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig


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
    """One human-readable line per validation failure, all of them."""
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.extend(
            f"{location}: {fragment.strip()}"
            for fragment in str(error["msg"]).split(";")
        )
    return lines
