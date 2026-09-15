"""
T05 — Input divergence.

Setup:     3 stable nodes, all with the same input.
Injection: set-input-single <target> with a strongly deviating value.
Expected:  The target uses the deviating input. In ShareInputs,
           `computation.inputs_agree` fires and returns
           InputsDivergent. All three nodes enter EM, the target is
           identified as divergent and excluded.

Note: what counts as "strongly deviating" depends on
`Computation::inputs_agree`. For the ETCS brake curve, inputs_agree
typically checks tolerances on current_speed, target_speed, and
available_distance. A value far outside those bounds should diverge.
"""
from harness.assertions import wait_cycles_advance, wait_peer_health

TARGET = 2

DIVERGENT_INPUT = {
    "current_speed": 999.0,
    "target_speed": 0.0,
    "available_distance": 10.0,
}


def test_input_divergence(fabric_3):
    ack = fabric_3.diag.targeted_input(TARGET, DIVERGENT_INPUT)
    assert ack, "targeted input not acknowledged"

    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]
    for survivor in survivors:
        peer = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=8.0)
        assert peer is not None

    assert wait_cycles_advance(fabric_3, survivors[0], n_cycles=5, timeout=8.0)
