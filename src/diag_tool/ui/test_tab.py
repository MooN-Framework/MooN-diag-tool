"""
Test tab: runs pytest against the scenarios directory.

Modes:
- simulated: local pytest as usual; conftest spawns nodes via cargo.
- hardware:  pytest is called with --fabric=hardware plus the GUI's
             multicast config. Optionally (see "Deploy binary + config
             before run") builds the Rust binary and pushes binary +
             config to every configured hardware node via SSH/SCP
             before launching pytest -- reusing the same
             ssh_deploy/cross_compile building blocks as the Timing
             tab, just generalised beyond the cycle-duration sweep.

Scenario feasibility (see core/scenario_meta.py): each scenario needs
either 3 or 4 nodes (fabric_3/fabric_4 fixture) and may rely on
helpers that don't work against real hardware (restart_node, log
pattern matching). In hardware mode the scenario list marks/greys out
what isn't feasible with the currently configured node count, and a
run with no explicit selection only picks feasible scenarios instead
of the whole directory.

pytest runs as a subprocess. Output is streamed line-by-line into the
UI via a signal (thread-safe).
"""
from __future__ import annotations

import html
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.cross_compile import CrossBuild
from ..core.mode import derive_mode
from ..core.os_open import OpenError, open_path
from ..core.scenario_meta import ScenarioMeta, analyze_scenarios, infeasible_reason
from ..core.ssh_deploy import (
    HardwareNode,
    SshError,
    enabled_nodes,
    ensure_remote_dirs,
    is_process_running,
    nodes_from_json,
    nodes_to_json,
    scp_bytes,
    scp_file,
    scp_get,
    scp_get_dir,
    ssh_exec,
    stop_moon_node_service,
    tail_remote_logs,
    write_hw_nodes_file,
)
from .hardware_nodes_dialog import HardwareNodesDialog

# harness lives alongside diag_tool in the same repo (src/harness),
# same import style as core/timing_sweep.py.
from harness.config_gen import make_spec, render_toml_str


# Simple pytest output tokenisation. We inspect each incoming line and
# assign a colour to the whole line. Specific tokens (PASSED / FAILED /
# etc.) inside long lines get an extra highlighted span.
COLOR_PASS  = "#23A55A"   # green
COLOR_FAIL  = "#DA373C"   # red
COLOR_WARN  = "#F0B232"   # yellow / orange
COLOR_INFO  = "#5A96F7"   # blue
COLOR_MUTED = "#949BA4"   # grey
COLOR_TEXT  = "#DBDEE1"

# Ordered: first match wins.
LINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bFAILED\b|\bERROR\b|\berror:", re.I), COLOR_FAIL),
    (re.compile(r"\bPASSED\b|\bok\b", re.I), COLOR_PASS),
    (re.compile(r"\bSKIPPED\b|\bXFAIL\b|\bXPASS\b|\bWARN(?:ING)?\b", re.I), COLOR_WARN),
    (re.compile(r"^\s*=+.*=+\s*$"), COLOR_INFO),   # === headers ===
    (re.compile(r"^\s*_+.*_+\s*$"), COLOR_INFO),   # ___ separators ___
    (re.compile(r"^\$"), COLOR_MUTED),              # our own $-prefixed command echo
    (re.compile(r"^\[pytest exit=(\d+)\]"), None),  # handled specially
]

TOKEN_HIGHLIGHTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bPASSED\b"),  COLOR_PASS),
    (re.compile(r"\bFAILED\b"),  COLOR_FAIL),
    (re.compile(r"\bERROR\b"),   COLOR_FAIL),
    (re.compile(r"\bSKIPPED\b"), COLOR_WARN),
    (re.compile(r"\bXFAIL\b"),   COLOR_WARN),
    (re.compile(r"\bXPASS\b"),   COLOR_WARN),
]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _colorize(line: str) -> str:
    """Return HTML for one line, wrapped in a colour span."""
    line = _ANSI_RE.sub("", line)  # drop ANSI escapes from pytest colour output
    esc = html.escape(line) if line else "&nbsp;"

    # exit-code line: green if 0, red otherwise
    m = re.match(r"^\[pytest exit=(\d+)\]", line)
    if m:
        color = COLOR_PASS if m.group(1) == "0" else COLOR_FAIL
        return f'<span style="color:{color};font-weight:600">{esc}</span>'

    # summary line like "= 12 passed, 1 failed in 3.4s ="
    if re.match(r"^=+.*\bpassed\b.*=+$", line) or re.match(r"^=+.*\bfailed\b.*=+$", line):
        color = COLOR_FAIL if "failed" in line or "error" in line else COLOR_PASS
        return f'<span style="color:{color};font-weight:700">{esc}</span>'

    # baseline line colour
    base = COLOR_TEXT
    for pat, colour in LINE_PATTERNS:
        if colour and pat.search(line):
            base = colour
            break

    # highlight individual result tokens with a bolder span (keeps the
    # line's baseline colour for the rest of the text).
    def sub_token(match):
        token = match.group(0)
        for pat, tc in TOKEN_HIGHLIGHTS:
            if pat.fullmatch(token):
                return f'<span style="color:{tc};font-weight:700">{html.escape(token)}</span>'
        return html.escape(token)

    highlighted = re.sub(
        r"\b(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b",
        sub_token, esc,
    )
    return f'<span style="color:{base}">{highlighted}</span>'


class TestTab(QWidget):
    line_received = Signal(str)
    _fetch_logs_done = Signal(bool)

    def __init__(self, settings_provider, settings_saver, parent=None) -> None:
        """
        settings_provider() -> current AppSettings
        settings_saver(new_settings) -> persists changes (used for the
            hardware node list, shared with the Timing tab)
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._save_settings = settings_saver
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_batch: bool = False
        self._reader: Optional[threading.Thread] = None
        self._scenario_meta: dict[str, ScenarioMeta] = {}

        self._build()
        self.line_received.connect(self._append_line)
        self._fetch_logs_done.connect(self._on_fetch_logs_done)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        # Header
        head = QHBoxLayout()
        title = QLabel("Test runner"); title.setProperty("heading", True)
        head.addWidget(title); head.addStretch(1)
        # Mode is derived, not chosen: hardware iff at least one hardware
        # node is configured below, simulated otherwise (see
        # _current_mode / _sync_mode_ui).
        self.mode_label = QLabel("")
        self.mode_label.setProperty("heading", True)
        head.addWidget(self.mode_label)
        self.refresh_btn = QPushButton("Load scenarios")
        self.refresh_btn.clicked.connect(self._reload_scenarios)
        head.addWidget(self.refresh_btn)
        self.select_feasible_btn = QPushButton("Select feasible")
        self.select_feasible_btn.setToolTip(
            "Select all scenarios that fit the currently configured "
            "hardware node count and don't rely on restart_node()."
        )
        self.select_feasible_btn.clicked.connect(self._select_feasible)
        head.addWidget(self.select_feasible_btn)
        outer.addLayout(head)

        # Hardware nodes row -- configuring at least one node here is what
        # switches the tab into hardware mode (see _current_mode).
        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel("Hardware nodes:"))
        self.hw_summary = QLabel("")
        self.hw_summary.setProperty("muted", True)
        hw_row.addWidget(self.hw_summary, 1)
        self.hw_nodes_btn = QPushButton("Configure hardware nodes…")
        self.hw_nodes_btn.clicked.connect(self._open_nodes_dialog)
        hw_row.addWidget(self.hw_nodes_btn)
        self.fetch_logs_btn = QPushButton("Fetch + open node logs")
        self.fetch_logs_btn.setToolTip(
            "SCP each configured node's remote_log_dir (all per-session "
            "file logs + the 'current' symlink from --log-dir) plus the "
            "raw remote_log back to <session logs>/hardware-node-logs/ "
            "and open that folder."
        )
        self.fetch_logs_btn.clicked.connect(self._on_fetch_logs)
        hw_row.addWidget(self.fetch_logs_btn)
        self.deploy_chk = QCheckBox("Deploy binary + config before run")
        self.deploy_chk.setChecked(True)
        self.deploy_chk.setToolTip(
            "Cross-compile the Rust binary and push binary + a fresh "
            "config to every configured hardware node via SSH/SCP, "
            "then (re)start it, before launching pytest."
        )
        hw_row.addWidget(self.deploy_chk)
        outer.addLayout(hw_row)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # Top: selection + invocation
        top = QWidget()
        tl = QHBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(14)

        scen_col = QVBoxLayout(); scen_col.setSpacing(6)
        lbl = QLabel("Scenarios"); lbl.setProperty("heading", True)
        scen_col.addWidget(lbl)
        hint = QLabel("Empty selection = run all · multi-select")
        hint.setProperty("muted", True)
        scen_col.addWidget(hint)
        self.scenarios_list = QListWidget()
        self.scenarios_list.setSelectionMode(QAbstractItemView.MultiSelection)
        scen_col.addWidget(self.scenarios_list)
        tl.addLayout(scen_col, 2)

        cfg_box = QGroupBox("Invocation")
        cl = QVBoxLayout(cfg_box); cl.setContentsMargins(12, 20, 12, 12); cl.setSpacing(10)
        cl.addWidget(QLabel("Extra pytest args:"))
        self.extra_args = QLineEdit("-v")
        cl.addWidget(self.extra_args)
        cl.addWidget(QLabel("Environment (KEY=VAL, one per line):"))
        self.env_edit = QPlainTextEdit()
        self.env_edit.setPlaceholderText("PYTEST_XDIST_AUTO_NUM_WORKERS=1")
        self.env_edit.setFixedHeight(80)
        cl.addWidget(self.env_edit)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("Start")
        self.run_btn.setProperty("accent", True)
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = QPushButton("Cancel")
        self.stop_btn.setProperty("danger", True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.run_btn); btn_row.addWidget(self.stop_btn)
        cl.addLayout(btn_row)

        self.help_btn = QPushButton("Hardware mode: how it works")
        self.help_btn.clicked.connect(self._show_patch)
        cl.addWidget(self.help_btn)
        cl.addStretch(1)
        tl.addWidget(cfg_box, 3)

        split.addWidget(top)

        # Bottom: output (QTextEdit so we can style per line via HTML)
        bottom = QWidget()
        bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 8, 0, 0); bl.setSpacing(6)
        h = QLabel("pytest output"); h.setProperty("heading", True)
        bl.addWidget(h)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Output appears here once a test is running.")
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.output.setFont(mono)
        self.output.setLineWrapMode(QTextEdit.NoWrap)
        bl.addWidget(self.output)
        split.addWidget(bottom)

        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)

        self._sync_mode_ui()

    # ---- mode toggle / hardware nodes -------------------------------------

    def _current_mode(self) -> str:
        """See core.mode.derive_mode -- shared with the Timing and
        Settings tabs so all three agree on what mode is active."""
        return derive_mode(self._get_settings())

    def _sync_mode_ui(self) -> None:
        is_hw = self._current_mode() == "hardware"
        self.mode_label.setText(f"Mode: {self._current_mode()}")
        self.deploy_chk.setEnabled(is_hw)
        self._refresh_hw_summary()
        if self.scenarios_list.count():
            self._reload_scenarios()

    def on_settings_changed(self, _settings) -> None:
        """Connected to Bus.settings_changed in main_window -- fires on
        EVERY settings apply, whichever tab triggered it (Settings
        tab's Apply button, or a hardware-nodes dialog save in this
        tab, the Timing tab, or the Package tab). Without this, only
        this tab's own dialog save refreshed its mode label/hw
        summary -- switching to hardware mode elsewhere left this tab
        showing a stale "Mode: simulated" until something in it
        happened to be clicked."""
        self._sync_mode_ui()

    def _refresh_hw_summary(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        enabled = enabled_nodes(nodes)
        if nodes:
            hosts = ", ".join(f"{n.node_id}:{n.host}" for n in enabled)
            suffix = f" — {hosts}" if hosts else " — none enabled"
            self.hw_summary.setText(f"{len(enabled)}/{len(nodes)} enabled{suffix}")
        else:
            self.hw_summary.setText("no hardware nodes configured")

    def _open_nodes_dialog(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        dlg = HardwareNodesDialog(nodes, parent=self)
        if dlg.exec():
            updated = dlg.collect()
            new = replace(s, hardware_nodes_json=nodes_to_json(updated))
            self._save_settings(new)
            self._append_line(f"[nodes] saved {len(updated)} hardware node(s)")
            self._sync_mode_ui()

    def _on_fetch_logs(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        if not nodes:
            QMessageBox.warning(self, "No nodes",
                                "Configure hardware nodes first.")
            return
        target_dir = Path(s.session_log_dir) / "hardware-node-logs"
        self.fetch_logs_btn.setEnabled(False)
        self._append_line(f"\n=== fetching logs for {len(nodes)} node(s) → {target_dir} ===")

        def worker() -> None:
            ok_any = False
            try:
                for hn in sorted(nodes, key=lambda n: n.node_id):
                    # Structured per-session logs (--log-dir): the whole
                    # directory, not just "current", so earlier sessions
                    # stay available for post-mortem after a restart.
                    node_dir = target_dir / f"node{hn.node_id}_{hn.host}"
                    try:
                        scp_get_dir(hn, hn.remote_log_dir, node_dir, timeout=60.0)
                        self.line_received.emit(f"[log] {hn.host} log dir → {node_dir}")
                        ok_any = True
                    except SshError as e:
                        self.line_received.emit(
                            f"[log] {hn.host} log dir FAILED (no --log-dir "
                            f"output yet?): {e.output}"
                        )
                    # Raw stdout+stderr catch-all alongside it.
                    raw_path = target_dir / f"node{hn.node_id}_{hn.host}_raw.log"
                    try:
                        scp_get(hn, hn.remote_log, raw_path, timeout=30.0)
                        self.line_received.emit(f"[log] {hn.host} raw log → {raw_path}")
                        ok_any = True
                    except SshError as e:
                        self.line_received.emit(f"[log] {hn.host} raw log FAILED: {e.output}")
            except Exception as e:
                # Safety net -- see _run_worker's comment: without this,
                # any unexpected error here would leave fetch_logs_btn
                # disabled forever with no visible error.
                self.line_received.emit(f"[log] unexpected {type(e).__name__}: {e}")
            self._fetch_logs_done.emit(ok_any)

        threading.Thread(target=worker, name="fetch-logs", daemon=True).start()

    @Slot(bool)
    def _on_fetch_logs_done(self, ok_any: bool) -> None:
        self.fetch_logs_btn.setEnabled(True)
        if not ok_any:
            return
        s = self._get_settings()
        target_dir = Path(s.session_log_dir) / "hardware-node-logs"
        try:
            open_path(target_dir)
        except OpenError as e:
            QMessageBox.warning(self, "Could not open folder", str(e))

    def _repo_root(self) -> Path:
        # __file__ = <repo>/src/diag_tool/ui/test_tab.py -> parents[3] = <repo>
        return Path(__file__).resolve().parents[3]

    def _scenarios_dir(self) -> Optional[Path]:
        # Prefer the in-repo tests/scenarios — since the harness lives
        # in this repo now, that's the canonical location. Only fall back
        # to a user-configured path if the in-repo one is missing (e.g.
        # someone reorganised the tree).
        in_repo = self._repo_root() / "tests" / "scenarios"
        if in_repo.is_dir():
            return in_repo
        s = self._get_settings()
        if s.scenarios_path:
            p = Path(s.scenarios_path)
            return p if p.is_dir() else None
        return None

    def _reload_scenarios(self) -> None:
        d = self._scenarios_dir()
        self.scenarios_list.clear()
        self._scenario_meta = {}
        if d is None:
            QMessageBox.warning(self, "No directory",
                                "Set 'Scenarios directory' in Settings first.")
            return

        self._scenario_meta = analyze_scenarios(d)
        is_hw = self._current_mode() == "hardware"
        node_count = len(enabled_nodes(nodes_from_json(self._get_settings().hardware_nodes_json)))

        for p in sorted(d.glob("test_*.py")):
            meta = self._scenario_meta[str(p)]
            tag = f"[{meta.required_nodes}N]"
            if meta.hw_notes:
                tag += "  ⚠"
            it = QListWidgetItem(f"{p.name}   {tag}")
            it.setData(Qt.UserRole, str(p))

            infeasible = is_hw and (
                meta.hw_status == "unsupported" or meta.required_nodes > node_count
            )
            if infeasible:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                it.setToolTip(infeasible_reason(meta, node_count))
                it.setForeground(QColor("#6A6F76"))
            elif meta.hw_notes:
                it.setToolTip("; ".join(meta.hw_notes))
            self.scenarios_list.addItem(it)

    def _select_feasible(self) -> None:
        for i in range(self.scenarios_list.count()):
            it = self.scenarios_list.item(i)
            it.setSelected(bool(it.flags() & Qt.ItemIsEnabled))

    def _on_stop(self) -> None:
        self._cancel_batch = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _on_run(self) -> None:
        s = self._get_settings()
        d = self._scenarios_dir()
        if d is None:
            QMessageBox.warning(self, "No directory",
                                "Set 'Scenarios directory' in Settings first.")
            return
        if not self._scenario_meta:
            self._reload_scenarios()

        selected = [self.scenarios_list.item(i).data(Qt.UserRole)
                    for i in range(self.scenarios_list.count())
                    if self.scenarios_list.item(i).isSelected()]

        mode = self._current_mode()
        hw_nodes = enabled_nodes(nodes_from_json(s.hardware_nodes_json)) if mode == "hardware" else []
        node_count = len(hw_nodes)
        skipped: list[str] = []

        if mode == "hardware":
            if selected:
                # Explicit selection: drop anything infeasible for the
                # current node count (list should already prevent this
                # via disabled items, but the node count may have
                # changed since the list was last loaded).
                feasible = []
                for path_str in selected:
                    meta = self._scenario_meta.get(path_str)
                    if meta is None or (
                        meta.hw_status != "unsupported" and meta.required_nodes <= node_count
                    ):
                        feasible.append(path_str)
                    else:
                        skipped.append(f"{Path(path_str).name}: {infeasible_reason(meta, node_count)}")
                selected = feasible
                if not selected:
                    QMessageBox.warning(self, "No feasible scenarios",
                                        "All selected scenarios are infeasible with the "
                                        "currently configured hardware nodes.")
                    return
            else:
                # No explicit selection: don't hand pytest the whole
                # directory (that would include scenarios that need
                # 4 nodes or restart_node()) -- only the feasible ones.
                selected = [
                    path_str for path_str, meta in sorted(self._scenario_meta.items())
                    if meta.hw_status != "unsupported" and meta.required_nodes <= node_count
                ]
                if not selected:
                    QMessageBox.warning(self, "No feasible scenarios",
                                        f"No scenario fits {node_count} configured hardware "
                                        f"node(s). Configure more nodes or pick a smaller set.")
                    return
        elif not selected:
            # Simulated mode, nothing explicitly selected: run every
            # discovered scenario individually (see the per-scenario
            # loop below) -- same net effect as the old "hand pytest
            # the whole directory" behaviour, just as an explicit file
            # list so each one gets its own subprocess.
            selected = sorted(self._scenario_meta.keys())
            if not selected:
                QMessageBox.warning(self, "No scenarios", f"No test_*.py files found in {d}.")
                return

        # Drop any scenario with zero actual test functions (e.g. a
        # module-level pytest.mark.skip with nothing left to skip, see
        # scenario_meta.py) regardless of mode or whether it got here
        # via explicit selection or auto-detection -- running it would
        # collect 0 items, pytest exits 5, and that's not a real
        # pass/fail worth a subprocess or a batch-summary line.
        runnable = []
        for path_str in selected:
            meta = self._scenario_meta.get(path_str)
            if meta is not None and not meta.has_tests:
                skipped.append(f"{Path(path_str).name}: {infeasible_reason(meta, node_count)}")
            else:
                runnable.append(path_str)
        selected = runnable
        if not selected:
            QMessageBox.warning(self, "No runnable scenarios",
                                "Every selected scenario has no test functions to run.")
            return

        cmd = [sys.executable, "-m", "pytest", "-s"]
        # -s (--capture=no): without it, pytest swallows ALL stdout
        # produced during a PASSING test -- including fixture setup,
        # which is exactly where _HardwareFabric.restart_all()'s
        # [restart]/[wait] lines come from. Those lines are the whole
        # point of hardware mode's per-test node restart being visible
        # at all; without -s they'd only ever show up in a failure
        # traceback, i.e. never on the happy path.
        # Force our own pytest.ini + conftest so we never accidentally
        # pick up a stale one that happens to sit next to whatever test
        # path the user selected (e.g. an older copy in the Rust repo).
        repo_root = self._repo_root()
        pytest_ini = repo_root / "pytest.ini"
        if pytest_ini.exists():
            cmd += ["-c", str(pytest_ini)]
        cmd += shlex.split(self.extra_args.text() or "")

        # Always pass --rust-repo when configured so the conftest's
        # `binary` fixture knows where to run cargo build.
        if s.rust_repo_path:
            cmd.append(f"--rust-repo={s.rust_repo_path}")

        hw_nodes_file: Optional[Path] = None
        if mode == "hardware":
            # conftest's hardware fabric needs the node connection data
            # (host/password/remote paths incl. remote_log_dir) to do
            # SSH restart_node() and to tail each node's --log-dir
            # current-log for wait_for_log() -- see conftest.py /
            # harness/hw_node.py. Written fresh per run, 0600, cleaned
            # up in _run_worker's finally. Shared across every
            # scenario's own pytest invocation below -- its content
            # doesn't change per scenario, only the running process
            # does (and that's conftest's job, freshly, per subprocess).
            fd, tmp_name = tempfile.mkstemp(prefix="diag-tool-hw-nodes-", suffix=".json")
            os.close(fd)
            hw_nodes_file = Path(tmp_name)
            write_hw_nodes_file(hw_nodes, hw_nodes_file)
            cmd += [
                "--fabric=hardware",
                f"--hw-nodes-file={hw_nodes_file}",
                f"--diag-group={s.diag_group}",
                f"--diag-port={s.diag_port}",
                f"--op-group={s.op_group}",
                f"--op-port={s.op_port}",
                f"--interface-ip={s.interface_ip}",
            ]

        # cwd is the diag_tool repo root (same as -c's directory).

        env = os.environ.copy()
        # Without this, the pytest child process's own stdout is
        # block-buffered (not a tty) regardless of -s, so print() lines
        # from restart_all()/wait_operational() can sit in the child's
        # buffer instead of streaming to this tab live -- they'd still
        # show up eventually (on flush/exit), just bunched up and late,
        # easy to mistake for "not happening at all".
        env["PYTHONUNBUFFERED"] = "1"
        for line in self.env_edit.toPlainText().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

        self.output.clear()
        for msg in skipped:
            self._append_line(f"[skip] {msg}")

        deploy_first = mode == "hardware" and self.deploy_chk.isChecked()

        self._cancel_batch = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._reader = threading.Thread(
            target=self._run_worker,
            args=(cmd, selected, repo_root, env, deploy_first, s, hw_nodes, hw_nodes_file, mode),
            name="test-run", daemon=True,
        )
        self._reader.start()

    # ---- deploy-then-run worker --------------------------------------------

    def _run_worker(self, base_cmd: list[str], scenarios: list[str], repo_root: Path, env: dict,
                     deploy_first: bool, s, hw_nodes: list[HardwareNode],
                     hw_nodes_file: Optional[Path], mode: str) -> None:
        try:
            self._run_worker_inner(base_cmd, scenarios, repo_root, env, deploy_first, s, hw_nodes, mode)
        except Exception as e:
            # Safety net: an unhandled exception anywhere in
            # _run_worker_inner (e.g. an unexpected error inside
            # _deploy_to_hardware/_blocking_build/CrossBuild) used to
            # kill this background thread silently without ever
            # emitting __RUN_DONE__ -- Start/Stop then stayed stuck in
            # "running" state forever with no visible error anywhere.
            self.line_received.emit(f"[error] unexpected {type(e).__name__}: {e}")
            self.line_received.emit("__RUN_DONE__")
        finally:
            if hw_nodes_file is not None:
                try:
                    hw_nodes_file.unlink(missing_ok=True)
                except OSError:
                    pass

    def _run_worker_inner(self, base_cmd: list[str], scenarios: list[str], repo_root: Path,
                           env: dict, deploy_first: bool, s, hw_nodes: list[HardwareNode],
                           mode: str) -> None:
        if deploy_first:
            if not hw_nodes:
                self.line_received.emit(
                    "[deploy] no hardware nodes configured — skipping deploy, "
                    "assuming nodes are already running"
                )
            elif not s.rust_repo_path:
                self.line_received.emit(
                    "[deploy] aborted: set 'Rust repository' in Settings first"
                )
                self.line_received.emit("__RUN_DONE__")
                return
            else:
                self.line_received.emit(
                    f"\n=== deploy: build + push to {len(hw_nodes)} node(s) ==="
                )
                features = [f.strip() for f in (s.build_features or "").split(",") if f.strip()]
                cross = CrossBuild(
                    repo_path=Path(s.rust_repo_path),
                    target_triple=s.target_triple,
                    features=features,
                    binary_name=s.binary_name or "node",
                )
                rc, bin_path = self._blocking_build(cross)
                if rc != 0 or bin_path is None:
                    self.line_received.emit("[deploy] aborted: build failed")
                    self.line_received.emit("__RUN_DONE__")
                    return
                if not self._deploy_to_hardware(hw_nodes, bin_path, s):
                    self.line_received.emit("[deploy] aborted: deploy failed")
                    self.line_received.emit("__RUN_DONE__")
                    return
                self.line_received.emit("=== deploy done, starting tests ===\n")

        if mode == "simulated":
            # ONE pytest invocation covering every scenario. Simulated
            # mode never had the state-leaking-between-tests problem
            # hardware mode had: fabric_N there spawns brand-new local
            # subprocesses on a brand-new tmp_path per TEST FUNCTION
            # already (pytest's normal function-scoped fixture
            # semantics are enough, no process-boundary trick needed).
            # Splitting into one subprocess per scenario here would
            # only re-run conftest's session-scoped `binary` fixture
            # (cargo build) once per scenario instead of once for the
            # whole batch -- pure overhead, no correctness benefit.
            self._run_single_pytest_invocation(base_cmd, scenarios, repo_root, env)
            return

        # Hardware mode: one pytest subprocess PER scenario,
        # sequentially -- not one subprocess covering every selected
        # scenario. This matters here specifically: conftest's fabric_N
        # fixture restarts every node fresh at the START of the first
        # test it sees in a process (see _HardwareFabric.restart_all())
        # -- giving each scenario its own subprocess means that ALWAYS
        # happens against a brand-new interpreter, rather than relying
        # only on pytest's in-process fixture scoping to reset state
        # between test FUNCTIONS within one shared process. Slower
        # (repeated pytest/conftest startup per scenario) but
        # bulletproof: no way for one scenario's leftover state (an
        # isolated node, an altered session_id, module-level caching)
        # to bleed into the next one.
        total = len(scenarios)
        results: list[tuple[str, int]] = []  # (scenario name, exit code)
        for i, scenario_path in enumerate(scenarios, start=1):
            if self._cancel_batch:
                self.line_received.emit("\n[cancelled] stopping before next scenario")
                break

            name = Path(scenario_path).name
            self.line_received.emit(f"\n=== [{i}/{total}] {name} ===")
            cmd = base_cmd + [scenario_path]
            self.line_received.emit(f"$ (cwd={repo_root}) " + " ".join(shlex.quote(c) for c in cmd))
            try:
                self._proc = subprocess.Popen(
                    cmd, cwd=repo_root, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except OSError as e:
                self.line_received.emit(f"[Error] pytest could not be started: {e}")
                results.append((name, -1))
                continue

            assert self._proc.stdout
            for line in self._proc.stdout:
                self.line_received.emit(line.rstrip("\n"))
            rc = self._proc.wait()
            self.line_received.emit(f"[{name}] exit={rc}")
            results.append((name, rc))

        # rc==5 is pytest's "no tests were collected" -- distinct from
        # an actual failure. The has_tests pre-filter in _on_run should
        # already keep such scenarios out of `scenarios` entirely, but
        # this stays as a defensive fallback (e.g. a scenario that
        # skips everything via a runtime condition rather than a static
        # module-level marker, which static analysis can't catch).
        passed = sum(1 for _, rc in results if rc == 0)
        no_tests = [(name, rc) for name, rc in results if rc == 5]
        failed = [(name, rc) for name, rc in results if rc not in (0, 5)]
        self.line_received.emit(
            f"\n=== batch done: {passed}/{len(results)} passed"
            + (f", {len(no_tests)} had no tests to run" if no_tests else "")
            + (f", {len(failed)} failed/errored" if failed else "")
            + " ==="
        )
        for name, rc in failed:
            self.line_received.emit(f"  FAILED {name} (exit={rc})")
        for name, rc in no_tests:
            self.line_received.emit(f"  NO TESTS {name} (exit={rc})")
        self.line_received.emit("__RUN_DONE__")

    def _run_single_pytest_invocation(self, base_cmd: list[str], scenarios: list[str],
                                       repo_root: Path, env: dict) -> None:
        """Simulated-mode path: every selected scenario handed to ONE
        pytest process in a single call, same as before per-scenario
        subprocess isolation was added for hardware mode. See the
        caller's comment for why simulated mode doesn't need (and
        shouldn't pay the cost of) splitting this into one subprocess
        per scenario."""
        cmd = base_cmd + scenarios
        self.line_received.emit(f"$ (cwd={repo_root}) " + " ".join(shlex.quote(c) for c in cmd))
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=repo_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as e:
            self.line_received.emit(f"[Error] pytest could not be started: {e}")
            self.line_received.emit("__RUN_DONE__")
            return

        assert self._proc.stdout
        for line in self._proc.stdout:
            self.line_received.emit(line.rstrip("\n"))
        rc = self._proc.wait()
        self.line_received.emit(f"[pytest exit={rc}]")
        self.line_received.emit("__RUN_DONE__")

    def _blocking_build(self, cross: CrossBuild) -> tuple[int, Optional[Path]]:
        """Run CrossBuild synchronously from within the worker thread,
        streaming its output the same way pytest output is streamed."""
        done = threading.Event()
        result: dict = {"rc": -1, "path": None}

        def on_done(rc: int, path: Optional[Path]) -> None:
            result["rc"] = rc
            result["path"] = path
            done.set()

        cross.start(on_line=lambda ln: self.line_received.emit(ln), on_done=on_done)
        done.wait()
        return result["rc"], result["path"]

    def _deploy_to_hardware(self, nodes: list[HardwareNode], bin_path: Path, s) -> bool:
        """Push the built binary + a freshly rendered config to every
        node, then (re)start it via its configured start_cmd. Same
        push/stop/start sequence as TimingSweep._hw_start, generalised
        for a plain test run instead of a cycle-duration sweep.

        Preparation phase first: stop moon-node.service on every node.
        If this node was ever deployed to via the Package tab, that
        systemd unit is still running the production binary from
        /opt/moon/bin/node -- resolved_stop_cmd() below only pkill's
        HardwareNode.remote_binary (default /opt/voting/node), a
        different path, so without this the production binary keeps
        running alongside the harness's test binary and both fight
        over the same multicast group/port.
        """
        nominal = len(nodes)
        for hn in sorted(nodes, key=lambda n: n.node_id):
            self.line_received.emit(f"[deploy] {hn.host} stopping moon-node.service (if present)")
            stop_moon_node_service(hn)
            warn = hn.validate_logging()
            if warn:
                self.line_received.emit(f"[deploy] WARNING: {warn}")
            try:
                ensure_remote_dirs(hn)
            except SshError as e:
                self.line_received.emit(f"[deploy] {hn.host} FAILED (mkdir): {e.output}")
                return False
            spec = make_spec(
                own_id=hn.node_id, nominal=nominal, minimum=2, cycle_ms=20,
                fabric_group=s.op_group, fabric_port=s.op_port,
                diag_group=s.diag_group, diag_port=s.diag_port,
                interface=s.node_network_interface,
                init_sync_timeout_ms=15_000,  # matches the new NodeSpec/make_spec default, kept explicit for clarity here
            )
            toml_text = render_toml_str(spec)
            # Kill any already-running instance of the harness's own
            # process BEFORE pushing a new binary over it -- stop_moon_node_service()
            # above only stops the systemd unit; a process this tool
            # itself started earlier via nohup+exec (e.g. a previous Test
            # tab or Timing tab run) is still running here otherwise, and
            # scp trying to overwrite a currently-executing binary file
            # fails with ETXTBSY ("text file busy"), which shows up as a
            # generic "dest open ... Failure" -- easy to mistake for a
            # missing-directory problem, it isn't one.
            try:
                ssh_exec(hn, hn.resolved_stop_cmd(), timeout=30.0)
            except SshError as e:
                self.line_received.emit(f"[deploy] {hn.host} stop_cmd failed (continuing): {e.output}")
            time.sleep(0.3)  # let the old process actually exit before we try to overwrite its binary

            self.line_received.emit(f"[deploy] {hn.host} → config {hn.remote_config}")
            try:
                scp_bytes(hn, toml_text.encode(), hn.remote_config)
                self.line_received.emit(f"[deploy] {hn.host} → binary {hn.remote_binary}")
                scp_file(hn, bin_path, hn.remote_binary, timeout=120.0)
            except SshError as e:
                self.line_received.emit(f"[deploy] {hn.host} FAILED (transfer): {e.output}")
                return False
            self.line_received.emit(f"[deploy] {hn.host} starting")
            try:
                ssh_exec(hn, hn.resolved_start_cmd(), timeout=30.0)
            except SshError as e:
                self.line_received.emit(f"[deploy] {hn.host} FAILED (start): {e.output}")
                return False
            # start_cmd exiting 0 only proves the shell backgrounded
            # something -- not that it's still alive. Verify for real.
            time.sleep(0.5)
            if not is_process_running(hn):
                tail = tail_remote_logs(hn)
                self.line_received.emit(
                    f"[deploy] {hn.host} FAILED: process not running after "
                    f"start_cmd (nohup/& exits 0 even on an immediate "
                    f"crash) -- log tail:\n{tail}"
                )
                return False
        return True

    @Slot(str)
    def _append_line(self, line: str) -> None:
        if line == "__RUN_DONE__":
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        self.output.append(_colorize(line))
        c = self.output.textCursor()
        c.movePosition(QTextCursor.End)
        self.output.setTextCursor(c)

    def _show_patch(self) -> None:
        # The hardware-mode writeup lives in README.md's "Hardware mode
        # internals" section (folded in from a former standalone doc
        # file) -- extract just that section rather than dumping the
        # whole README into a popup.
        pkg_dir = Path(__file__).resolve().parent.parent.parent.parent
        readme = pkg_dir / "README.md"
        text = "README.md not found."
        if readme.exists():
            full = readme.read_text()
            marker = "## Hardware mode internals"
            start = full.find(marker)
            if start != -1:
                end = full.find("\n## ", start + len(marker))
                text = full[start:end if end != -1 else None].strip()
            else:
                text = full
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Hardware mode: how it works")
        dlg.setTextFormat(Qt.MarkdownText)
        dlg.setText(text)
        dlg.exec()
