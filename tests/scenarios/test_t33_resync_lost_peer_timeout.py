"""
T33 — ResyncLostPeerTimeout.

Why skipped:
    The framework's rejoin detection does NOT run the way originally
    assumed. The `GoResyncLostPeer` trigger is emitted in InitSync
    when a non-InitSync frame is received during the discovery phase
    (a sign that peers are already well into cycle operation and we
    are showing up "late"). That sets `state.set_was_lost(true)`, and
    on discovery-complete the transition `GoResyncLostPeer →
    ResyncLostPeer` fires.

    T33 tries to silence the peers before the restart via
    `silent(0, 50)` and `silent(1, 50)`, so the rejoining node gets no
    responses during resync → ResyncLostPeerTimeout. But:

    1. `silent` disables send loops in peers on a cycle basis. Time
       passes between the `silent` injection and the point where
       `restart_node(2)` actually gets a running process into
       InitSync -- the peers are already active again by the time
       node 2's InitSync phase runs.
    2. Even if peers were silent: with peer frames completely absent,
       node 2 lands in InitialSyncTimeout, not in rejoin detection.

    Observed behaviour (per the session log): node 2 starts fresh,
    receives peer frames, and goes through InitSync → PeerSync →
    cycle operation completely normally. The rejoin path is never
    entered at all.

Implementation paths if this timeout needs to be tested after all:
    - Framework extension: an injection that specifically sets
      `state.set_was_lost(true)` at a node's startup. The node would
      then complete InitSync cleanly, see was_lost=true, and
      transition to ResyncLostPeer. Combined with `silent` peers, the
      timeout could then be triggered directly.
    - A second config override `resync_returning_timeout_ms=3`
      combined with the was_lost injection above.

    Without this extension, the timeout is not deterministically
    reproducible within the existing injection API.

Coverage:  (ResyncLostPeer, ResyncLostPeerTimeout) → ErrorManagement
           NOT verified by an automated test.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Rejoin detection doesn't trigger as expected -- needs a "
    "framework extension (set_was_lost injection). See docstring."
)


def test_resync_lost_peer_timeout(fabric_3):
    pass
