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

conftest.py's `_HardwareFabric` (see README.md's "Hardware mode
internals" section) backs
hardware mode with harness.hw_node.RemoteNode, so both of the
previously-degraded helper groups now actually work there, given
--hw-nodes-file (which the diag tool's Test tab always supplies when
it runs in hardware mode):
  - `restart_node` -> stop_cmd/start_cmd over SSH on the real remote
    process (see _HardwareFabric.restart_node)
  - log-pattern helpers (`any_node_reached_failsafe`,
    `assert_exclusion_confirmed`, or a scenario's own
    `node.wait_for_log(...)`) -> RemoteNode tails the node's
    `--log-dir` current-session log over SSH, so these match the same
    way they do against a local subprocess
  - `wait_node_died` -> unchanged, already worked uniformly in both
    modes since it polls GetStatus via `fabric.diag`

No scenario is marked hw-"unsupported" purely for using one of these
helpers anymore -- only a node-count mismatch (fabric_4 scenario,
fewer than 4 hardware nodes configured) still makes one infeasible.
restart_node() gets a lightweight informational note instead (still
hw_status "ok") since it's worth knowing a scenario power-cycles a
real remote process before running it unattended.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Informational only (hw_status stays "ok") -- worth flagging in the
# UI, but no longer a reason to grey a scenario out.
_RESTARTS_REAL_HARDWARE = ("restart_node",)


@dataclass(frozen=True)
class ScenarioMeta:
    path: Path
    required_nodes: int            # 3 or 4, from the fabric_3/fabric_4 fixture used
    hw_status: str                 # "ok" | "unsupported"
    hw_notes: tuple[str, ...]      # human-readable notes, empty if none apply
    has_tests: bool = True         # False: file defines zero `def test_*` functions (e.g.
                                    # unconditionally pytest.mark.skip'd at module level with
                                    # nothing left to skip) -- running it collects 0 items and
                                    # pytest exits 5 ("no tests ran"), not a real pass/fail.

    @property
    def unsupported(self) -> bool:
        return self.hw_status == "unsupported"


_TEST_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)


def analyze_scenario(path: Path) -> ScenarioMeta:
    text = path.read_text(errors="ignore")

    required_nodes = 4 if re.search(r"\bfabric_4\b", text) else 3
    has_tests = bool(_TEST_FUNC_RE.search(text))

    notes: list[str] = []

    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in _RESTARTS_REAL_HARDWARE):
        notes.append(
            "restart_node() -- power-cycles the real node process on "
            "the hardware over SSH (stop_cmd/start_cmd)"
        )
    if not has_tests:
        notes.append(
            "no test function in this file (only a module-wide "
            "pytest.mark.skip or similar) -- running it would collect "
            "0 items, pytest exit=5, not a real pass/fail"
        )

    return ScenarioMeta(
        path=path, required_nodes=required_nodes,
        hw_status="ok", hw_notes=tuple(notes), has_tests=has_tests,
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
    if not meta.has_tests:
        reasons.append("no test function in this file")
    if meta.required_nodes > configured_node_count:
        reasons.append(
            f"needs {meta.required_nodes} node(s), "
            f"only {configured_node_count} configured"
        )
    if meta.hw_status == "unsupported":
        reasons.extend(meta.hw_notes)
    return " / ".join(reasons)
