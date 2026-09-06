"""
Timing analysis: minimum achievable cycle duration (synchronization frequency).

Goal:
    Determine the smallest stable ``cycle_duration_ms`` at which the
    fabric stays in the operational state over a measurement window,
    without ``cycle overrun`` warnings exceeding the tolerance
    threshold.

Method:
    1. Sweep in descending order over a list of candidates.
    2. For each candidate, scale the in-cycle offsets proportionally
       (keeping the 5 : 10 : 14 / 20 ratio from the defaults).
    3. Start the fabric, let it run for MEASURE_S seconds, evaluate
       the logs.
    4. A candidate counts as "stable" if:
         - wait_operational succeeds within SETUP_TIMEOUT_S,
         - no node died,
         - the overrun fraction per node is <= MAX_OVERRUN_FRACTION.
    5. At the end, report the smallest stable value; the report is
       written as a text artifact into work_dir.

This test is meant as a standalone study and is marked ``timing`` in
``pytest.ini`` to exclude it from normal CI runs
(``pytest -m timing``).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from harness.config_gen import derive_timing
from harness.fabric import Fabric, FabricOptions


# --------- Sweep parameters (centralized for tuning) ---------
CANDIDATES_MS = [20, 15, 12, 10, 8, 6, 5, 4, 3, 2]
NOMINAL = 3
MINIMUM = 2
MEASURE_S = 8.0
SETUP_TIMEOUT_S = 15.0
MAX_OVERRUN_FRACTION = 0.02  # <=2% of cycles may overrun
# ---------------------------------------------------------------


_OVERRUN_RE = re.compile(r"cycle overrun.*overrun_us=(\d+)")
_CYCLE_RE = re.compile(r"cycle duration.*cycle_us=(\d+)")


def _scaled_timing(cycle_ms: int) -> dict:
    """
    Full timing section for this cycle duration, derived by the shared
    generator so a sweep candidate cannot end up with scaled in-cycle
    offsets next to unscaled timeouts.

    A cycle_ms that is too small raises ValueError, which the sweep
    treats as an unusable candidate — the same verdict it would reach
    from the node's own validate().
    """
    return derive_timing(cycle_ms)


def _analyze_node_log(log_path: Path) -> tuple[int, int, list[int]]:
    """
    Reads the node log and counts cycles as well as overruns.
    Returns: (n_cycles, n_overruns, overrun_us_list).
    """
    n_cycles = 0
    overruns: list[int] = []
    try:
        for line in log_path.read_text(errors="replace").splitlines():
            if _CYCLE_RE.search(line):
                n_cycles += 1
            m = _OVERRUN_RE.search(line)
            if m:
                overruns.append(int(m.group(1)))
    except FileNotFoundError:
        pass
    return n_cycles, len(overruns), overruns


@pytest.mark.timing
def test_minimum_cycle_duration(binary: Path, work_dir: Path):
    """
    Sweeps over CANDIDATES_MS and reports the smallest stable cycle
    duration at the end. The test NEVER fails hard on an individual
    candidate -- it's a measurement. The only assertion is that at
    least one candidate was stable.
    """
    report_lines: list[str] = []
    report_lines.append(
        f"# Timing sweep: {NOMINAL}oo{MINIMUM}, measure={MEASURE_S}s, "
        f"max_overrun={MAX_OVERRUN_FRACTION:.1%}"
    )
    report_lines.append(
        "cycle_ms | operational | mean_cycle_us | overrun_frac | worst_overrun_us | verdict"
    )
    report_lines.append("-" * 90)

    smallest_stable: int | None = None

    for cycle_ms in CANDIDATES_MS:
        cand_dir = work_dir / f"cycle_{cycle_ms}ms"
        cand_dir.mkdir(parents=True, exist_ok=True)

        opts = FabricOptions(
            nominal=NOMINAL,
            minimum=MINIMUM,
            binary=binary,
            work_dir=cand_dir,
            cycle_duration_ms=cycle_ms,
            timing_overrides=_scaled_timing(cycle_ms),
        )
        fab = Fabric(opts=opts)

        operational = False
        mean_us = 0
        overrun_frac = 1.0
        worst = 0
        verdict = "FAIL"

        try:
            fab.start_all()
            operational = fab.wait_operational(timeout=SETUP_TIMEOUT_S)
            if not operational:
                verdict = "NO_OPERATIONAL"
            else:
                time.sleep(MEASURE_S)

                any_died = any(not n.is_running() for n in fab.nodes.values())
                if any_died:
                    verdict = "NODE_DIED"

                # Aggregate the logs per node.
                total_cycles = 0
                total_overruns = 0
                worst_all = 0
                mean_accum = 0
                mean_count = 0
                for nid in fab.nodes:
                    log_path = cand_dir / "logs" / f"node_{nid}.log"
                    nc, no_, ovs = _analyze_node_log(log_path)
                    total_cycles += nc
                    total_overruns += no_
                    if ovs:
                        worst_all = max(worst_all, max(ovs))

                    # Extract the mean cycle_us.
                    try:
                        text = log_path.read_text(errors="replace")
                        for m in _CYCLE_RE.finditer(text):
                            mean_accum += int(m.group(1))
                            mean_count += 1
                    except FileNotFoundError:
                        pass

                mean_us = mean_accum // max(1, mean_count)
                overrun_frac = total_overruns / max(1, total_cycles)
                worst = worst_all

                if not any_died and overrun_frac <= MAX_OVERRUN_FRACTION:
                    verdict = "STABLE"
                    smallest_stable = cycle_ms
                elif not any_died:
                    verdict = "TOO_MANY_OVERRUNS"
        except Exception as e:
            verdict = f"EXC:{type(e).__name__}"
        finally:
            fab.stop_all()

        report_lines.append(
            f"{cycle_ms:>8} | {str(operational):>11} | {mean_us:>13} | "
            f"{overrun_frac:>12.3%} | {worst:>16} | {verdict}"
        )

        # If we're already "unstable" and go lower still, it's very
        # likely to stay unstable. We run the sweep to the end anyway,
        # since sometimes the overhead becomes paradoxically better
        # measurable with shorter send_intervals. No optimization here.

    report = "\n".join(report_lines)
    (work_dir / "timing_sweep_report.txt").write_text(report + "\n")
    print("\n" + report + "\n")

    assert smallest_stable is not None, (
        "no candidate was stable -- the environment is too jittery, "
        "or the sweep range is wrong"
    )
    print(f"\nSmallest stable cycle duration: {smallest_stable} ms "
          f"({1000 / smallest_stable:.1f} Hz)")
