"""
T14 — Stale frames from clock drift.

Setup:     3 stable nodes.
Injection: none available. See below.
Expected:  Frames that arrive after `stale_frame_threshold_ms` are
           discarded on ingest without the fabric leaving regular
           cycle operation.

Why skipped:
    The full test would set `resync_interval_cycles` very high and run
    for many minutes until accumulated clock drift pushes frames past
    the staleness threshold. That is too slow for a routine run.

    The approximation -- setting `stale_frame_threshold_ms` extremely
    low (~1 ms) so that even minimally delayed frames are discarded --
    is not reliable on loopback, where delivery jitter is both small
    and bursty. The result flips between "nothing discarded at all"
    and "everything discarded", neither of which reproduces the drift
    end state.

Implementation path if this needs to be covered after all:
    - Framework extension: an injection that delays a peer's outgoing
      frames by a configurable amount, so staleness can be provoked
      directly instead of via drift.
    - The test would then assert the discard log line on the receiver
      plus continued cycle progress on all nodes.

Coverage:  stale-frame discard on ingest
           NOT verified by an automated test.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Needs a dedicated Rust injection for frame delay -- "
           "the tight-threshold trick isn't reliable on loopback"
)


def test_stale_frames(fabric_3):
    """Placeholder so the scenario is collected and reported as
    skipped rather than silently contributing zero test items.

    The body is intentionally left unimplemented: the skip above is
    unconditional, and writing assertions against an injection that
    does not exist yet would only create the appearance of coverage.
    """
    raise AssertionError("unreachable: see module-level pytestmark")
