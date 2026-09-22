"""Read the tracker's write surface off the roles it is dialled as.

The census needs three facts about the running code, and derives all three
rather than restating them: which roles a dialled tracker writes through,
where a step enters the verifier, and which function a derived-write
declaration names.  A role added beside the port, a renamed verifier
parameter or a moved declaration therefore changes what is censused,
instead of leaving a list here to disagree with the code.
"""

import functools
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, get_type_hints

import kodezart
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.composition.tracker import DialledTracker
from kodezart.core import protocols
from kodezart.core.protocols import WriteBackStep
from kodezart.domain.derived_writes import derived_writes
from kodezart.types.domain.write_adoption import DriveEntry, Source

#: The installed package the census reads.
PACKAGE_ROOT = Path(kodezart.__file__).parent


def tracker_write_roles() -> tuple[type, ...]:
    """Every role a dialled tracker writes the backend through.

    A tracker is dialled as more than the port: a role declared beside it
    and handed the same session writes the backend exactly as a port member
    does, and a surface read off the port alone would stop seeing such a
    write the moment it existed (KOD-829).  The roles are the fields of the
    dialled tracker whose annotation is declared with the port, which is
    the one place their number is already stated.
    """
    return tuple(
        annotation
        for annotation in get_type_hints(DialledTracker).values()
        if isinstance(annotation, type) and annotation.__module__ == protocols.__name__
    )


class Defined(Protocol):
    """A definition the census can address: where it lives and what it is."""

    __module__: str
    __qualname__: str


def source_address(subject: Defined) -> Source:
    """Where *subject* is defined, addressed as the census addresses it."""
    path = sys.modules[subject.__module__].__file__
    if path is None:
        raise ValueError(f"{subject.__qualname__} has no source file to address")
    return Source(
        module=Path(path).relative_to(PACKAGE_ROOT).as_posix(),
        function=subject.__qualname__,
    )


def drive_entry() -> DriveEntry:
    """Where a step enters the verifier, read off the verifier and the step."""
    annotated = [
        name
        for name, annotation in get_type_hints(WriteBackVerifier.write_back).items()
        if annotation is WriteBackStep
    ]
    members = [
        name
        for name in dir(WriteBackStep)
        if not name.startswith("_")
        and not isinstance(getattr(WriteBackStep, name), property)
    ]
    if len(annotated) != 1 or len(members) != 1:
        raise ValueError("the verifier takes exactly one step, with one member")
    return DriveEntry(
        verifier=source_address(WriteBackVerifier.write_back),
        step_parameter=annotated[0],
        step_method=members[0],
    )


def marker_address() -> Source:
    """Where the derived-write declaration is defined."""
    return source_address(derived_writes)


@functools.cache
def installed_sources() -> Mapping[str, str]:
    """Every installed module, keyed by its path inside the package."""
    return {
        path.relative_to(PACKAGE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
    }
