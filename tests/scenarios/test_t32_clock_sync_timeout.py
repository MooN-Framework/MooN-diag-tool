"""
T32 — ClockSyncTimeout (cold start).

Why skipped:
    Reliably triggering this timeout requires an extremely short
    `clock_sync_timeout_ms` (below what even a single Cristian round
    trip needs). At values <=1, config validation panics
    ("send_interval must be smaller than smallest timeout"). At
    values 2-5, the node never reaches the ClockSync transition in
    InitSync at all (presumably because discovery and ClockSync share
    internals that get validated too).

    Larger values (>=10ms) make the trigger non-deterministic --
    depending on loopback latency and scheduling, ClockSync can
    complete successfully.

Implementation paths if this timeout needs to be tested after all:
    - Framework extension: an injection that blocks the send path
      between InitialSync-complete and the first ClockSync send. Then
      ClockSync would time out without any Cristian responses.
    - A separate validation rule: exempt `clock_sync_timeout_ms` from
      the general "smallest timeout" rule, so a value close to
      send_interval is allowed.

    Without this extension, the timeout is not deterministically
    reproducible within the existing injection API and config
    constraints.

Coverage:  (ClockSync, ClockSyncTimeout) → Failsafe
           NOT verified by an automated test.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Config validation + discovery internals block a "
    "reproducible ClockSyncTimeout trigger. See docstring."
)


def test_clock_sync_timeout(fabric_3):
    pass
