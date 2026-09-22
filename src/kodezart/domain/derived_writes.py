"""Declare beside a writer the tracker writes it holds out of the seam."""

from collections.abc import Callable


def derived_writes[F](*methods: str) -> Callable[[F], F]:
    """State that this function's calls of *methods* are derived writes.

    A derived write carries a state move or a fact any process can
    recompute from what the tracker already holds, so there is no authored
    claim for a second reader to judge and no window to re-read it in.  The
    declaration stands beside the write it holds out, where the next person
    to change that body sees it, and is exact in both directions: the
    adoption census refuses a write no declaration covers, and refuses a
    declaration whose function no longer makes that write.

    Nothing is wrapped: the declaration is read off the source, so a
    decorated function behaves exactly as an undecorated one does.
    """
    if not methods:
        raise ValueError("a derived-write declaration names the writes it holds out")

    def declared(function: F) -> F:
        return function

    return declared
