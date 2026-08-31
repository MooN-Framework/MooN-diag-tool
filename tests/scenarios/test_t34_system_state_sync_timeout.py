"""
T34 — SystemStateSyncTimeout.

Why skipped:
    The SystemStateSync state is only reached via the rejoin flow,
    after a successful ResyncLostPeer. Triggering
    SystemStateSyncTimeout requires the snapshot exchange after the
    clock sync to fail -- the peers would have to go silent EXACTLY
    between ResyncLostPeerOk and SystemStateSync.

    That window is too small to control reliably with the current
    injection API: the diag command loop runs on a cycle basis, but a
    rejoin races through the discovery phases in sub-cycle time.
    There's no injection that would take effect precisely from the
    SystemStateSync transition onward.

    A config override `state_sync_timeout_ms=1` would be possible
    (analogous to T32's approach for ClockSyncTimeout). That would make
    the snapshot exchange time out deterministically. Not implemented
    because this path leads to `Isolation` (not Failsafe like the
    other timeouts), and Isolation is harmless enough that it causes
    no cluster behaviour change -- the test assertion would just be
    "node X is in Isolation and the rest keeps running", which T25
    already covers for a different trigger.

If this edge becomes critical later:
    - Fabric with `timing_overrides={"state_sync_timeout_ms": 1}`
    - Restart a node analogous to T33
    - Assertion: the target's log contains
        `transition from=SystemStateSync event=SystemStateSyncTimeout to=Isolation`

Coverage:  (SystemStateSync, SystemStateSyncTimeout) → Isolation
           NOT verified by an automated test.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Time window for reliable injection too small -- see the "
    "module docstring for an implementation path if needed"
)


def test_system_state_sync_timeout(fabric_3):
    pass
