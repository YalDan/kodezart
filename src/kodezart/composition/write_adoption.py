"""Refuse boot over a tracker write path nothing accounts for.

The census needs three facts about the running code, and derives all three
rather than restating them: which roles a dialled tracker writes through,
where a step enters the verifier, and which function a derived-write
declaration names.  A role added beside the port, a renamed verifier
parameter or a moved declaration therefore changes what is censused,
instead of leaving a list here to disagree with the code.

``verify_write_adoption`` is the boot gate: the first act of the
application's lifespan, before the tracker is dialled or anything written,
over the installed source.  The census does not read configuration, because
a write path no verifier drives is a defect in every deployment whatever it
schedules.
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
from kodezart.domain.errors import UnverifiedWritePathError
from kodezart.domain.write_adoption import artifact_writes, take_census
from kodezart.types.domain.write_adoption import DriveEntry, Source, WriteCensus

#: The installed package the census reads.
PACKAGE_ROOT = Path(kodezart.__file__).parent
#: The one path a tree with no tracker write at all is refused under, so a
#: packaging change that hid the source cannot make the gate pass on nothing.
NO_WRITE_FOUND = "(no call of the tracker's write surface was found)"


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


def verify_write_adoption(sources: Mapping[str, str] | None = None) -> WriteCensus:
    """Refuse a tracker write no verifier drives and no declaration holds out.

    Reads the installed source unless *sources* is given, and returns the
    census it took.  A census is kept per distinct source content, so a
    process pays the parse once and a changed tree is censused afresh
    rather than answered from what an earlier tree said.  A stale
    declaration does not refuse: a declared function that no longer writes
    is no write path.
    """
    read = installed_sources() if sources is None else sources
    census = _census(tuple(sorted(read.items())))
    if not census.sites:
        raise UnverifiedWritePathError(paths=(NO_WRITE_FOUND,))
    if census.unadopted:
        raise UnverifiedWritePathError(paths=census.paths)
    return census


@functools.cache
def _census(sources: tuple[tuple[str, str], ...]) -> WriteCensus:
    return take_census(
        sources=dict(sources),
        writes=artifact_writes(tracker_write_roles()),
        entry=drive_entry(),
        marker=marker_address(),
    )
