"""Addresses in the installed tree, and the census of its tracker writes."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Source:
    """One production definition: its module path and its qualified name."""

    module: str
    """The posix path of the module inside the package, e.g. ``services/x.py``."""
    function: str
    """The qualified name inside that module, e.g. ``ScopeTerminal._post``."""

    def __str__(self) -> str:
        return f"{self.module}::{self.function}"


@dataclass(frozen=True, order=True)
class CallSite:
    """One call of a tracker artifact write, at the function holding it."""

    module: str
    function: str
    method: str

    def __str__(self) -> str:
        return f"{self.module}::{self.function}::{self.method}"

    @property
    def holder(self) -> Source:
        """The function this call stands in."""
        return Source(module=self.module, function=self.function)


@dataclass(frozen=True)
class DriveEntry:
    """Where a step enters the verifier, read off the real classes.

    Nothing here is spelled by hand: the caller derives the verifier's
    address, the parameter it takes its step on and the member that step
    must answer, so a verifier that renamed either would be read as it is
    rather than as this census once believed it to be.
    """

    verifier: Source
    """The method a step is handed to."""
    step_parameter: str
    """The parameter of that method annotated as a step."""
    step_method: str
    """The one member a step declares, which the verifier drives."""


@dataclass(frozen=True)
class WriteCensus:
    """Every tracker artifact write in a tree, and what accounts for it."""

    sites: frozenset[CallSite]
    """Every call of the write surface the tree makes through a receiver."""
    driven: frozenset[CallSite]
    """The calls whose holding function the verifier drives."""
    held_out: frozenset[CallSite]
    """The undriven calls their holding function declares as derived."""
    unadopted: frozenset[CallSite]
    """The calls neither driven nor held out, and held-out authored prose."""
    stale: frozenset[CallSite]
    """A declared write its holding function no longer makes undriven."""
    authored: frozenset[CallSite]
    """The calls whose writer composes content it authored."""

    @property
    def paths(self) -> tuple[str, ...]:
        """Every unadopted write path, named in full and sorted."""
        return tuple(sorted(str(site) for site in self.unadopted))
