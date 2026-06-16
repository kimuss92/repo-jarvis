from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from core.state_store import StateStore


SystemStateSnapshot = Any


@dataclass
class VerificationExpectation:
    """An expectation checked against the OS state snapshot."""
    name: str
    verifier: Callable[[SystemStateSnapshot], Awaitable[bool]]


async def run_action_with_verification(
    state: StateStore,
    execute_action: Callable[[], Awaitable[Any]],
    expectations: List[VerificationExpectation],
    verify_delay_s: float = 0.35,
    max_verify_s: float = 3.0,
) -> Any:
    """Executes an action, then spawns a background verifier.
    The verifier will poll state snapshots until expectations are satisfied or
    timeout occurs. If unmet, a drift flag is written to StateStore."""
    result = await execute_action()


    async def _verify_job() -> None:
        await asyncio.sleep(verify_delay_s)
        deadline = time.time() + max_verify_s

        satisfied: Dict[str, bool] = {e.name: False for e in expectations}

        while time.time() < deadline and not all(satisfied.values()):
            snap = await state.snapshot()
            for e in expectations:
                if satisfied[e.name]:
                    continue
                try:
                    ok = await e.verifier(snap)
                    if ok:
                        satisfied[e.name] = True
                except Exception:
                    pass
                await asyncio.sleep(0.15)

        if not all(satisfied.values()):
            await state.add_drift_flag(
                {
                    "ts": time.time(),
                    "result": str(result),
                    "expectations": satisfied,
                }
            )

    asyncio.create_task(_verify_job(), name="action_verification")
    return result
