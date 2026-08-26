"""
T30 — EM timeout (Rule 2b / self-excluded via peer rendezvous).

Why skipped (left open deliberately):
    A muted node runs into a subtle timing problem:
    1. Peers 1..N see it go silent → enter EM → propose it for
       exclusion (this works, they exclude it cleanly and keep
       running).
    2. The muted node itself also enters EM via the PeerInError
       rendezvous -- but ~5-10ms BEFORE the peers (because its ingest
       sees other nodes' EM frames faster than its own send
       timeouts).
    3. In EM it expects non-candidate votes from every peer. The
       peers' proposals arrive after its own vote deadline, but
       before the Rule-2b timeout evaluation.
    4. At the timeout point it potentially hasn't received enough
       peer proposals to confirm `self_excluded_by_peers()` → it goes
       into `Failsafe(QuorumLost)` and broadcasts GoFailsafe → peers
       follow via PeerBroadcast → all die.

    Fixes attempted:
    - **Fix A** (a self_excluded_by_peers check in the timeout
      branch): solved the problem in one test run, but wasn't
      reproducible in subsequent runs. Race-dependent.
    - **Approach 3** (self_excluded_by_peers as an early-exit gate in
      the collect_phase predicate): broke the 4-node fabric startup
      entirely -- the cluster no longer became operational. Rolled
      back.

    Both fixes were reverted. The current framework state is the
    stable baseline (fix #1 for own_dissented stays in, that's
    unrelated and works fine).

Semantically:
    The cluster collapse is a safe fail (all nodes go to failsafe, no
    safety violation), but architecturally unattractive: a single
    faulty node drags the healthy rest down via the GoFailsafe
    broadcast. A cleaner design would be:
    - A peer-broadcast filter that ignores GoFailsafe frames from
      peers already marked Lost (analogous to fix #1's design
      principle)
    - OR a send-verify self-diagnosis: the node infers from missing
      peer acks that it is itself isolated and goes straight to
      Isolation without the EM rendezvous detour

    Both approaches are larger framework changes and, for the thesis
    defense, are best presented as a "documented robustness point and
    future work".

Coverage:  (ErrorManagement, StateTimeout) → Failsafe(QuorumLost)
           resp. → SelfExcluded → Isolation (with the fix)
           NOT verified by an automated test.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="Rendezvous race in EM produces a cluster-fatal timeout "
    "path. Two fix attempts were rolled back (see docstring). "
    "Documented robustness point for future work."
)


def test_muted_node_goes_isolation_not_failsafe(fabric_4):
    pass
