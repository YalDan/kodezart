"""A run-time trap on the artifact directory (KOD-96-AC-29, KOD-96-AC-30).

The static guard in ``tests/test_artifact_directory_sites.py`` reads the
source, so a spelling it does not follow is a read it does not see.  This
trap watches what the process does instead: one audit hook, installed once
for the process with ``sys.addaudithook``, observes every file open,
directory listing, directory creation, removal, rename and subprocess the
wrapped flows make, whatever the spelling that made it.

Audit hooks cannot be removed, so the hook records only while a context
variable holds a record list (:func:`trapped`), and does nothing otherwise.
Tasks created inside a trapped block inherit the variable with the rest of
their context, so an ``await`` chain is observed too.

What it records while active:

- ``open``: the path as given (str, bytes or path-like, decoded) and the
  mode or flags, so a write is told from a read;
- ``os.listdir``, ``os.scandir`` and ``os.mkdir``;
- ``os.remove``, ``os.rename`` and ``shutil.rmtree``;
- ``subprocess.Popen``: the executable and every argv element, the shell
  string included;
- ``os.system``: the command.

A record is kept when any path, argv element or command holds the
directory's name as a path segment, split on ``/``, ``\\``, ``:`` and
whitespace; a relative path is taken from the working directory.  The
name is the constant's value, read off the constant.

The one writer's own writes are permitted and nothing else is: an
``os.mkdir``, and an ``open`` for writing, made while a frame of the module
``GitArtifactPersister`` is defined in is on the stack.  The frame's module
is read off the frame, and the writer's module off the class.

The reach is what the wrapped flows do: a read in a module or on a flow
that no wrapped test drives is not observed (a committed test holds that),
and neither is what a child process does beyond its own command line.
"""

import contextlib
import contextvars
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from kodezart.adapters.git.artifact_persister import GitArtifactPersister
from kodezart.core import constants

#: The directory, read off the constant rather than spelled here.
DIRECTORY: str = constants.ARTIFACT_DIR
#: The module the one writer is defined in, read off the class.
WRITER_MODULE: str = GitArtifactPersister.__module__
#: What splits a path, an argv element or a command into its segments.
SEGMENTS = re.compile(r"[/\\:\s]+")
#: Each recorded event, and the positions of its arguments that carry a
#: path, an argv or a command.
CARRIERS: dict[str, tuple[int, ...]] = {
    "open": (0,),
    "os.listdir": (0,),
    "os.scandir": (0,),
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "shutil.rmtree": (0,),
    "subprocess.Popen": (0, 1),
    "os.system": (0,),
}
#: The events whose carriers are paths, so a relative one is resolved.
PATH_EVENTS = frozenset(CARRIERS) - {"subprocess.Popen", "os.system"}
#: The ``os.open`` flags that make an open a write.
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


@dataclass(frozen=True)
class Record:
    """One act on a path under the directory, as the audit hook saw it."""

    event: str
    values: tuple[str, ...]
    writes: bool
    from_writer: bool


_ACTIVE: contextvars.ContextVar[list[Record] | None] = contextvars.ContextVar(
    "artifact_trap", default=None
)


def decoded(value: object) -> tuple[str, ...]:
    """*value* as text: a str, bytes or path-like, or each item of a sequence.

    One level of sequence is read (an argv), so the walk is bounded.
    """
    items = value if isinstance(value, (list, tuple)) else (value,)
    found: list[str] = []
    for item in items:
        if isinstance(item, (str, bytes, os.PathLike)):
            found.append(os.fsdecode(item))
    return tuple(found)


def holds_the_directory(value: str) -> bool:
    """Whether *value* holds the directory's name as a path segment."""
    return DIRECTORY in SEGMENTS.split(value)


def writes(event: str, args: tuple[object, ...]) -> bool:
    """Whether an ``open`` event opens for writing, by its mode or flags."""
    if event != "open":
        return False
    mode = args[1] if len(args) > 1 else None
    if isinstance(mode, str):
        return any(letter in mode for letter in "wax+")
    flags = args[2] if len(args) > 2 else 0
    return isinstance(flags, int) and bool(flags & WRITE_FLAGS)


def from_writer() -> bool:
    """Whether a frame of the writer's module is on the calling stack.

    The walk follows ``f_back`` and stops at the stack's end, bounded by the
    interpreter's recursion limit.
    """
    frame = sys._getframe(1)
    for _ in range(sys.getrecursionlimit()):
        if frame is None:
            return False
        if frame.f_globals.get("__name__") == WRITER_MODULE:
            return True
        frame = frame.f_back
    return False


def _hook(event: str, args: tuple[object, ...]) -> None:
    carriers = CARRIERS.get(event)
    if carriers is None:
        return
    records = _ACTIVE.get()
    if records is None:
        return
    values = tuple(
        text
        for position in carriers
        if position < len(args)
        for text in decoded(args[position])
    )
    if event in PATH_EVENTS:
        values = tuple(os.path.join(os.getcwd(), value) for value in values)
    if not any(holds_the_directory(value) for value in values):
        return
    records.append(
        Record(
            event=event,
            values=values,
            writes=writes(event, args),
            from_writer=from_writer(),
        )
    )


sys.addaudithook(_hook)


def permitted(record: Record) -> bool:
    """Whether *record* is one of the writer's own writes."""
    return record.from_writer and (
        record.event == "os.mkdir" or (record.event == "open" and record.writes)
    )


def unpermitted(records: list[Record]) -> list[Record]:
    """Every record that is not one of the writer's own writes."""
    return [record for record in records if not permitted(record)]


@contextlib.contextmanager
def trapped() -> Iterator[list[Record]]:
    """Record every act on a path under the directory while the block runs."""
    records: list[Record] = []
    token = _ACTIVE.set(records)
    try:
        yield records
    finally:
        _ACTIVE.reset(token)


@contextlib.contextmanager
def nothing_read_under_the_directory() -> Iterator[None]:
    """Run the block trapped, then refuse any act but the writer's writes."""
    with trapped() as records:
        yield
    found = unpermitted(records)
    assert found == [], found
