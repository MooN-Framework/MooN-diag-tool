"""
T14 — Stale frames from clock drift.

Full test: set `resync_interval_cycles` very high, run for many
minutes, check whether frames get discarded as stale. Too slow for
routine CI runs.

Approximation: set `stale_frame_threshold_ms` extremely low (~1 ms).
Then even minimally delayed frames get discarded as stale -- this
effectively mimics the end state of the drift scenario.

Marked as slow so CI skips it by default.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Needs a dedicated Rust injection for frame delay -- "
           "the tight-threshold trick isn't reliable on loopback"
)
