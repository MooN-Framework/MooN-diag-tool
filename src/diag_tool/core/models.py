"""Datenmodelle — Spiegel der Rust-Domänenobjekte.

Diese Typen sind bewusst als reine Dataclasses gehalten (kein Qt-Import),
damit sie in Worker-Threads und Tests ohne QApplication verwendbar sind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class NodeState(str, Enum):
    """Phasen des Node-State-Machine — muss mit Rust-Enum abgestimmt sein."""

    INIT = "init"
    SYNC = "sync"
    VOTING = "voting"
    DEGRADED = "degraded"
    FAIL_SAFE = "fail_safe"
    UNKNOWN = "unknown"


class VoteOutcome(str, Enum):
    AGREED = "agreed"
    OUTVOTED = "outvoted"
    NO_QUORUM = "no_quorum"


@dataclass(frozen=True, slots=True)
class NodeStatus:
    node_id: int
    state: NodeState
    seq: int
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class VotingResult:
    seq: int
    outcome: VoteOutcome
    brake_curve_value: float | None
    participating_nodes: tuple[int, ...]
    timestamp: datetime


@dataclass(slots=True)
class SystemSnapshot:
    """Aktueller Gesamtzustand — wird von der Monitor-View gerendert."""

    nodes: dict[int, NodeStatus] = field(default_factory=dict)
    last_vote: VotingResult | None = None
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
