# Fix: Probation-Peer-Ausfall triggert faelschlich Failsafe

## Problem

In `src/framework/runner/phases.rs` triggert `handle_error_management`
Failsafe wenn `tolerable_failures_remaining() == 0` VOR der Vote-Runde
ist — auch dann, wenn die ausgeschlossenen Peers ohnehin nur Probation-
Peers waren, die gar nicht zum Voting-Pool gehoert haben.

Szenario:
- 3oo2 Fabric, alle Alive → tolerable=1
- Peer stirbt → excluded → Lost → tolerable=0
- Peer kommt zurueck → Probation (nicht im voting_peer_count) → tolerable=0
- Peer stirbt nochmal → excluded → Lost → tolerable=0
  → `no_buffer_before_vote=true` → **Failsafe**

Aber: der voting-Pool ist unveraendert (2 Alive vor + 2 Alive nach dem
Vote). Wir haben nichts verloren, das zaehlte.

## Fix

Der `no_buffer_before_vote`-Check ist eine "zero margin" Sicherheit:
"Wenn wir keine Reserve mehr haben und JETZT einen Peer verlieren, ist
das Ende." Er darf aber nur feuern wenn *ein wirklicher* Voting-Peer
in diesem Vote verloren ging. Ausschluss eines Probation-Peers
reduziert `voting_peer_count()` nicht.

Diff gegen `src/framework/runner/phases.rs`, `handle_error_management`:

```diff
     pub(super) fn handle_error_management(&mut self) -> StateEvent {
         self.state.reset_exclusion_proposals();
         let _ = self.take_peer_in_error();

         let suppress = self.is_muted() || self.should_drop_vote();
         if suppress {
             warn!("injection: suppressing exclusion vote send");
         }

         let own_proposal = self.state.proposed_exclusions();
         let node_state = self.state.node_state();
         let deadline = std::time::Instant::now() + self.timing.error_mgmt_timeout;

+        // Snapshot the voting-eligible pool BEFORE the vote so we can
+        // tell after applying transitions whether an Alive peer was
+        // actually excluded (vs. a Probation peer, which never counted).
+        let voting_pool_before = 1 + self.state.active_peer_count_alive_only();
+
         let outcome = self.collect_phase(
             /* ...unchanged... */
         );

         let _ = self.take_peer_in_error();

         match outcome {
             super::PhaseOutcome::Complete => {
                 let no_buffer_before_vote = self.state.tolerable_failures_remaining() == 0;

                 if self.state.self_excluded_by_peers() {
                     /* ...unchanged... */
                     return StateEvent::SelfExcluded;
                 }

                 let confirmed = self.state.aggregate_exclusion_votes();
                 let transitions = self.state.apply_confirmed_exclusions(confirmed);
                 if transitions > 0 {
                     info!(
                         transitions,
                         confirmed = confirmed.as_u8(),
                         "health transitions"
                     );
                 }

                 self.state.start_new_cycle(self.next_cycle_tick());
                 if !self.state.quorum_available() {
                     self.mark_failsafe(FailsafeReason::QuorumLost);
                     return StateEvent::TooFewNodes;
                 }
-                if no_buffer_before_vote {
+
+                // "No buffer before the vote" only mandates failsafe if
+                // this vote actually shrank the voting pool. Excluding
+                // a Probation peer leaves the pool unchanged — there is
+                // nothing new to worry about.
+                let voting_pool_after = 1 + self.state.active_peer_count_alive_only();
+                let voting_pool_shrank = voting_pool_after < voting_pool_before;
+                if no_buffer_before_vote && voting_pool_shrank {
                     self.mark_failsafe(FailsafeReason::QuorumLost);
                     return StateEvent::TooFewNodes;
                 }
                 StateEvent::StateOk
             }
             super::PhaseOutcome::Timeout => {
                 self.mark_failsafe(FailsafeReason::QuorumLost);
                 StateEvent::StateTimeout
             }
             super::PhaseOutcome::Fault => {
                 self.mark_failsafe(FailsafeReason::LocalFault);
                 StateEvent::Fault
             }
         }
     }
```

## Was der Fix erhaelt

- **T11 (quorum_limit_2oo3)** faellt weiter durch — wenn Node 1 gekilled
  wird, war der Voting-Pool 3→2 (Alive-Peer verloren), `voting_pool_shrank=true`.
  Failsafe wie vorgesehen.
- **T20 (healthy fails during probation)** faellt weiter durch — der
  ausgefallene Node ist Alive (nicht Probation), voting-Pool schrumpft.

## Was der Fix repariert

- **T21 (probation fails during resync)** laeuft jetzt gruen — Voting-
  Pool bleibt gleich, kein Failsafe.
