"""
T18 — Sink safety failsafe.

Setup:     3 stable nodes.
Injection: broadcast_input with a value that guarantees
           emergency_brake=true (high current_speed, small
           available_distance).
Expected:  All three nodes reach consensus on emergency=true.
           Sink.evaluate returns Failsafe. The publisher still
           publishes the emergency-brake decision, then all three
           nodes go into failsafe (via SinkSafetyViolation).
"""
from harness.assertions import wait_node_died

# 200 m/s down to a standstill with only 20 m of remaining distance:
# the braking distance required is so much larger than what is
# available that emergency_brake is guaranteed to be set --
# unbrakeable across the entire deceleration table.
UNSAFE_INPUT = {
    "current_speed": 200.0,
    "target_speed": 0.0,
    "available_distance": 20.0,
}


def test_sink_safety_failsafe(fabric_3):
    fabric_3.diag.broadcast_input(UNSAFE_INPUT)

    # All three nodes must reach failsafe (process exit).
    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=10.0), (
            f"node {nid} should go to failsafe via SinkSafetyViolation"
        )

    # At least one node must have logged the sink rejection.
    found = False
    for node in fabric_3.nodes.values():
        if node.wait_for_log(r"sink rejected decision as unsafe", timeout=1.0):
            found = True
            break
    assert found, "no node logged the sink rejection"
