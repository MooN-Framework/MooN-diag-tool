"""
T23 — Input provider fail (one-shot).

Setup:     3 stable nodes.
Injection: input_provider_fail <target>.
Expected:  The target skips record_own_input in the next
           handle_read_inputs → ShareInputs finds own_input=None →
           Fault → Failsafe(LocalFault). Peers time out in
           ShareInputs, enter EM, exclude the target. The target
           broadcasts GoFailsafe, peers follow via PeerBroadcast.

Assertion: Log-based (deterministic from the on-disk log). The
           cluster collapses quickly after the fault; get_status-based
           assertions would race against process exit.

Coverage:  handle_read_inputs InputProviderFailed → Failsafe(LocalFault)
"""
from harness.assertions import wait_last_failsafe_reason, wait_node_died
from harness.diag import FAILSAFE_REASON

TARGET = 2


def test_input_provider_fail(fabric_3):
    assert fabric_3.diag.input_provider_fail(TARGET) is not None, (
        "input_provider_fail injection not acknowledged"
    )

    node = fabric_3.nodes[TARGET]

    # Primary evidence: the injection log entry.
    assert node.wait_for_log(
        r"injection: skipping record_own_input",
        timeout=6.0,
    ), "the target should log the injection"

    # Failsafe reason from the log (instead of get_status, which races
    # against process exit).
    assert node.wait_for_log(
        r"failsafe entered.*reason=LocalFault",
        timeout=6.0,
    ), "the target should reach failsafe with reason=LocalFault"

    # Bonus: structural assertion in case the target still responds.
    # Best-effort -- not a hard assert.
    _ = wait_last_failsafe_reason(
        fabric_3, TARGET, FAILSAFE_REASON["LocalFault"], timeout=1.5,
    )

    # All three nodes end in failsafe (target directly, peers via
    # PeerBroadcast).
    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=10.0), (
            f"node {nid} should end in failsafe"
        )
