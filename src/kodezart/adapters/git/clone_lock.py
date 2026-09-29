"""One lock per repository directory, shared within a process.

The clone cache moves local heads after a fetch and the worktree provider
checks heads out. Holding the directory's lock around both keeps a worktree
from being added on a head between the cache reading that head as free and
moving it.
"""

import asyncio
import os
from weakref import WeakKeyDictionary

_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    WeakKeyDictionary()
)


def clone_lock(repo_dir: str) -> asyncio.Lock:
    """The lock for *repo_dir* on the running event loop."""
    loop = asyncio.get_running_loop()
    by_dir = _LOCKS.setdefault(loop, {})
    return by_dir.setdefault(os.path.realpath(repo_dir), asyncio.Lock())
