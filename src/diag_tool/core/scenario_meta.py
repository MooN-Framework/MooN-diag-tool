"""
Static-analysis metadata for pytest test scenarios.

Scans each scenario file's source text for well-known signals so the
GUI can show, without executing anything, whether a scenario:
  - needs 3 or 4 nodes (fabric_3 vs fabric_4 fixture)
  - runs against real hardware as-is or is unsupported there

This is text/regex based on purpose. Scenarios are plain pytest files
that call a small, fixed set of harness fixtures and helper functions
(see harness/fabric.py, harness/assertions.py, conftest.py); matching
those names is enough and stays trivially maintainable as new
scenarios get added, without needing to import/execute anything.

conftest.py's `_HardwareFabric` / `_NullNode` (see docs/harness_hardware.md)
degrade the following in hardware mode:
  - `restart_node` -> raises NotImplementedError (node has to be
    restarted physically/manually)
  - `wait_node_died` -> works uniformly in both modes since it polls
    GetStatus via `fabric.diag` (see harness/assertions.py); no
    hardware-specific handling needed here anymore
  - log-pattern helpers (`any_node_reached_failsafe`,
    `assert_exclusion_confirmed`) -> `Node.wait_for_log` is a stub in
    hardware mode (no local log file), so these never match
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Helpers whose hardware-mode fallback (see conftest.py) makes the
# scenario impossible to run unattended against real nodes.
_UNSUPPORTED_HELPERS = ("restart_node",)

# Helpers that rely on local log files, which don't exist in hardware
# mode (Node.wait_for_log is a no-op stub there). Scenarios using only
# these still run, but the corresponding assertion will never succeed.
_LOG_PATTERN_HELPERS = ("any_node_reached_failsafe", "assert_exclusion_confirmed")


@dataclass(frozen=True)
class ScenarioMeta:
    path: Path
    required_nodes: int            # 3 or 4, from the fabric_3/fabric_4 fixture used
    hw_status: str                 # "ok" | "needs_adaptation" | "unsupported"
    hw_notes: tuple[str, ...]      # human-readable reasons, empty if hw_status == "ok"


def analyze_scenario(path: Path) -> ScenarioMeta:
    text = path.read_text(errors="ignore")

    required_nodes = 4 if re.search(r"\bfabric_4\b", text) else 3

    notes: list[str] = []
    status = "ok"

    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in _UNSUPPORTED_HELPERS):
        status = "unsupported"
        notes.append(
            "restart_node() -- in Hardware-Mode nicht implementiert "
            "(Node muss manuell neu gestartet werden)"
        )

    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in _LOG_PATTERN_HELPERS):
        if status == "ok":
            status = "needs_adaptation"
        notes.append(
            "nutzt Log-Pattern-Matching -- kein lokales Log in Hardware-Mode, "
            "Assertion greift dort nicht"
        )

    return ScenarioMeta(
        path=path, required_nodes=required_nodes,
        hw_status=status, hw_notes=tuple(notes),
    )


def analyze_scenarios(directory: Path) -> dict[str, ScenarioMeta]:
    """Analyze every test_*.py in `directory`, keyed by str(path)."""
    return {
        str(p): analyze_scenario(p)
        for p in sorted(directory.glob("test_*.py"))
    }


def infeasible_reason(meta: ScenarioMeta, configured_node_count: int) -> str:
    """Human-readable reason a scenario can't run against the currently
    configured hardware nodes -- empty string if it can."""
    reasons: list[str] = []
    if meta.required_nodes > configured_node_count:
        reasons.append(
            f"braucht {meta.required_nodes} Knoten, "
            f"nur {configured_node_count} konfiguriert"
        )
    if meta.hw_status == "unsupported":
        reasons.extend(meta.hw_notes)
    return " / ".join(reasons)
