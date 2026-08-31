"""
T3 — Silent before input.

Like T1, except the target swallows its own input entirely (instead
of the results/acks). Peers time out in ShareInputs, enter EM, and
exclude the target. The target itself goes blind via the CycleSync
timeout → Failsafe → GoFailsafe → all three reach failsafe.
"""
from harness.assertions import wait_node_died

TARGET = 2


def test_silent_before_input(fabric_3):
    assert fabric_3.diag.drop_inputs(TARGET, 1) is not None, (
        "drop_inputs injection not acknowledged"
    )

    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=15.0), (
            f"node {nid} should reach failsafe after silent-before-input"
        )
