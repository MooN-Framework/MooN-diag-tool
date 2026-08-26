"""
T19 — Byzantine 2/2 split in 2oo4.

Setup:     4 stable nodes (fabric_4).
Injection: fake_crc_multi([2, 3], 1) — arm both targets ATOMICALLY in
           a single broadcast telegram, so they send a fake CRC in
           the same cycle.
Expected:  Two nodes send 0xDEADBEEF, two send their real CRC. From
           the point of view of the two genuine nodes, this produces
           a 2/2 split of the CRCs -- strict_majority can no longer be
           determined. They mark_failsafe(StateDivergence) → Failsafe
           → GoFailsafe broadcast. The two fake senders follow the
           broadcast. Result: all four go to failsafe.

Semantics: with n=4, strict_majority needs at least 3 matching votes.
2/2 is exactly the Byzantine case from the 2oo4 analysis -- it can no
longer be safely determined who is telling the truth, so the
conservative reaction is failsafe.

Change from an earlier version of this test: sequential fake_crc
calls never overlapped due to RPC latency (node 3 was armed 15 cycles
after node 2, by which point node 2 was long isolated). The
multi-target call arms both nodes atomically in the same broadcast.
"""
from harness.assertions import wait_node_died

TARGETS = [2, 3]


def test_byzantine_split_2oo4(fabric_4):
    assert fabric_4.diag.fake_crc_multi(TARGETS, count=1), (
        "fake_crc_multi injection not acknowledged"
    )

    for nid in fabric_4.nodes:
        assert wait_node_died(fabric_4, nid, timeout=15.0), (
            f"node {nid} should reach failsafe on a 2/2 CRC split"
        )
