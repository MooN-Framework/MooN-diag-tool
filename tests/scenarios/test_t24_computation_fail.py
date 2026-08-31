"""
T24 — Computation fail (one-shot).

Setup:     3 stable nodes.
Injection: computation_fail <target>.
Expected:  In the next cycle, the target does not call the compute
           step in handle_share_inputs; instead it faults immediately
           with mark_failsafe(LocalFault). Peers time out in
           ShareResult (the target delivered an input but no result)
           → EM → exclude the target. The target broadcasts
           GoFailsafe.

Assertion: Log-based. We explicitly check the injection log entry to
           make sure the injection path was taken and not a genuine
           compute error.

Coverage:  handle_share_inputs computation-injection → Failsafe(LocalFault)
"""
from harness.assertions import wait_last_failsafe_reason, wait_node_died
from harness.diag import FAILSAFE_REASON

TARGET = 2


def test_computation_fail(fabric_3):
    assert fabric_3.diag.computation_fail(TARGET) is not None, (
        "computation_fail injection not acknowledged"
    )

    node = fabric_3.nodes[TARGET]

    # Explicitly check the injection log -- evidence that the
    # injection path was taken, not a regular compute error.
    assert node.wait_for_log(
        r"injection: forcing computation failure",
        timeout=6.0,
    ), "the target should log the injection"

    # Failsafe with LocalFault in the log.
    assert node.wait_for_log(
        r"failsafe entered.*reason=LocalFault",
        timeout=6.0,
    ), "the target should reach failsafe with reason=LocalFault"

    # Bonus structural check.
    _ = wait_last_failsafe_reason(
        fabric_3, TARGET, FAILSAFE_REASON["LocalFault"], timeout=1.5,
    )

    # All three nodes end in failsafe.
    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=10.0), (
            f"node {nid} should end in failsafe"
        )
