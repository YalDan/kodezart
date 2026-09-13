"""Characterize the native backend's missing conditional-write primitive."""

import asyncio

from tests.services.test_run_surface_lease import DURATION, JOB, _Board, outcome, surface
from tests.tracker.conftest import CLAIMED_ISSUE


async def test_inflight_write_after_successor_acquisition():
    board = _Board()
    board.pause = lambda name, args: name == "save_comment" and str(args.get("body", "")).startswith("[fixture-outcome:")
    task = asyncio.create_task(outcome(board))
    async with asyncio.timeout(10):
        await board.reached.wait()
        board.advance(DURATION + 10)
        surfaces = frozenset({surface(f"[fixture-outcome:{CLAIMED_ISSUE}:{JOB}]")})
        successor = board.tracker()
        await successor.acquire_surfaces(surfaces=surfaces, holder="successor", lease_seconds=DURATION)
        board.resume.set()
        await task
    assert len(board.outcome_comments()) == 1
    assert all("holder: successor\n" in c.body for c in board.grants())
    print("REPRODUCED: a save issued under a live lease landed after expiry and successor acquisition; pre-write checks cannot fence a delayed backend save.")
    await successor.release_surfaces(surfaces=surfaces, holder="successor")
