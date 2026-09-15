"""
Shared "simulated vs hardware" mode derivation.

Used identically by the Test tab, Timing tab, and Settings tab so all
three agree on what mode is currently active. Used to live as three
separate (and drift-prone) copies of the same three lines, one per
tab -- pulled out here so there's exactly one place that knows the
rule, which matters now that the Settings tab also needs it (to
decide whether to show the hardware build/target fields).
"""
from __future__ import annotations

from .settings import AppSettings
from .ssh_deploy import enabled_nodes, nodes_from_json


def derive_mode(s: AppSettings) -> str:
    """
    "auto" (default): derived from whether at least one hardware node
    is both configured and enabled. "simulated"/"hardware": forced
    regardless of what's configured -- see the Settings tab's Test
    mode dropdown.
    """
    if s.test_mode in ("simulated", "hardware"):
        return s.test_mode
    return "hardware" if enabled_nodes(nodes_from_json(s.hardware_nodes_json)) else "simulated"


def is_hardware_mode(s: AppSettings) -> bool:
    return derive_mode(s) == "hardware"
