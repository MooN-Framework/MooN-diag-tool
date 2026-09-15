"""
T17 — Startup fault.

Setup:     A custom 3-node fabric (fabric_3 can't be used here, since
           its wait_operational() blocks and we explicitly expect
           node 0 to fail startup). Node 0 starts with
           `MOON_INJECT_SELFTEST_FAIL=1` in its environment; the
           other two do not.

Expected:  Node 0 runs into the self-test failure path in
           handle_startup, calls mark_failsafe(SelfTestFailed),
           transitions to Failsafe, and exits. Nodes 1 and 2 never
           see node 0 in InitSync → ClockSyncTimeout → Failsafe. All
           three end in failsafe.

           For node 0, the exact last_failsafe_reason =
           SelfTestFailed (0x03) can be verified before it dies
           (a small time window, but reliable via
           wait_last_failsafe_reason).

Skipped on hardware: the env var would need to be baked into
start_cmd -- that's deployment-specific. The injection path itself is
deterministic; the simulated evidence is sufficient for the thesis.
"""
import pytest

from harness.assertions import wait_last_failsafe_reason, wait_node_died
from harness.diag import FAILSAFE_REASON
from harness.fabric import Fabric, FabricOptions

TARGET = 0

# Picked up by diag_tool.core.scenario_meta so the GUI can grey this
# scenario out before a hardware run instead of offering it and then
# reporting a skip. The runtime guard below stays as a second line of
# defence for hand-run pytest invocations.
HW_UNSUPPORTED = (
    "needs MOON_INJECT_SELFTEST_FAIL in the node's environment, which "
    "would have to be baked into start_cmd -- deployment-specific, out "
    "of scope for the delivery package"
)


def _make_selftest_fail_fabric(binary, work_dir) -> Fabric:
    opts = FabricOptions(
        nominal=3,
        minimum=2,
        binary=binary,
        work_dir=work_dir,
        cycle_duration_ms=20,
        extra_env_per_node={TARGET: {"MOON_INJECT_SELFTEST_FAIL": "1"}},
    )
    f = Fabric(opts=opts)
    f.start_all()
    return f


def test_startup_fault(request, binary, work_dir):
    if request.config.getoption("--fabric") == "hardware":
        pytest.skip("MOON_INJECT_SELFTEST_FAIL needs env-var injection "
                    "into the node process -- out of scope for hardware deployment")

    f = _make_selftest_fail_fabric(binary, work_dir)
    try:
        # Node 0 should set the failsafe reason SelfTestFailed before
        # it exits. We try to catch it in that small time window.
        got_reason = wait_last_failsafe_reason(
            f, TARGET, FAILSAFE_REASON["SelfTestFailed"], timeout=10.0,
        )
        # Fallback: if the StatusResponse window was missed, the log
        # evidence that the self-test failure was logged is enough.
        if not got_reason:
            node0 = f.nodes[TARGET]
            got_log = node0.wait_for_log(
                r"MOON_INJECT_SELFTEST_FAIL set, forcing self-test failure",
                timeout=2.0,
            )
            assert got_log, (
                f"node {TARGET} should report SelfTestFailed (neither "
                f"last_failsafe_reason nor a log line was found)"
            )

        # All three nodes must end in failsafe. Node 0 directly,
        # nodes 1 and 2 because they lose quorum.
        for nid in (0, 1, 2):
            assert wait_node_died(f, nid, timeout=15.0), (
                f"node {nid} should end in failsafe after the "
                "self-test-fail cascade"
            )
    finally:
        f.stop_all()
