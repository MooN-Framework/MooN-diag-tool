"""
T4 — Silent in CycleSync.

The target sends no state beacons in CycleSync. From the peers'
point of view, CycleSync times out → EM → the target is excluded.
The target itself goes into failsafe via blindness. The GoFailsafe
broadcast then takes down all three.
"""
from harness.assertions import wait_node_died

TARGET = 2


def test_silent_cyclesync(fabric_3):
    assert fabric_3.diag.drop_cyclesync(TARGET, 1) is not None, (
        "drop_cyclesync injection not acknowledged"
    )

    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=15.0), (
            f"node {nid} should reach failsafe after silent-cyclesync"
        )
