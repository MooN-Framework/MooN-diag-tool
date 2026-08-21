"""
Timing tab.

Three vertical sections:
  1. Cross-compile & deploy panel (Rust target triple, cargo build,
     deploy binary via SCP to all hardware nodes).
  2. Sweep parameters (candidates, nominal/minimum, measure_s,
     overrun tolerance).
  3. Live results table with colour-coded verdicts.

Modes:
  simulated — spawn local Rust processes with generated TOML configs.
  hardware  — SSH-restart already-installed nodes with new configs.

All heavy work happens in worker threads; UI updates go through Qt
signals.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.cross_compile import CrossBuild
from ..core.ssh_deploy import HardwareNode, SshError, enabled_nodes, nodes_from_json, nodes_to_json, scp_file
from ..core.timing_sweep import (
    CandidateResult,
    SweepParams,
    TimingSweep,
    VERDICT_NO_OP,
    VERDICT_NODE_DIED,
    VERDICT_OVERRUNS,
    VERDICT_STABLE,
    VERDICT_EXCEPTION,
)
from .hardware_nodes_dialog import HardwareNodesDialog


HEADERS = ["cycle_ms", "operational", "cycles", "mean_us",
           "overrun_frac", "worst_overrun_us", "verdict", "detail"]

VERDICT_COLORS = {
    VERDICT_STABLE:    QColor("#23A55A"),
    VERDICT_OVERRUNS:  QColor("#F0B232"),
    VERDICT_NO_OP:     QColor("#DA373C"),
    VERDICT_NODE_DIED: QColor("#DA373C"),
    VERDICT_EXCEPTION: QColor("#DA373C"),
}


class TimingTab(QWidget):
    _build_line = Signal(str)
    _build_done = Signal(int, object)
    _sweep_line = Signal(str)
    _sweep_result = Signal(object)
    _sweep_done = Signal(object)
    _deploy_line = Signal(str)
    _deploy_done = Signal(bool)
    _sweep_ready = Signal(str, object)   # mode, SweepParams -- predeploy succeeded
    _predeploy_failed = Signal()

    def __init__(self, settings_provider, settings_saver, parent=None) -> None:
        """
        settings_provider() -> current AppSettings
        settings_saver(new_settings) -> persists changes (used for the
            hardware node list, target triple etc.)
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._save_settings = settings_saver
        self._sweep: Optional[TimingSweep] = None
        self._build_helper: Optional[CrossBuild] = None
        self._built_binary_path: Optional[Path] = None

        self._build_ui()

        self._build_line.connect(self._append_build_line)
        self._build_done.connect(self._on_build_done)
        self._sweep_line.connect(self._append_sweep_line)
        self._sweep_result.connect(self._append_result_row)
        self._sweep_done.connect(self._on_sweep_done)
        self._deploy_line.connect(self._append_build_line)
        self._deploy_done.connect(self._on_deploy_done)
        self._sweep_ready.connect(self._on_sweep_ready)
        self._predeploy_failed.connect(self._on_predeploy_failed)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20); outer.setSpacing(14)
        s = self._get_settings()

        # Header
        head = QHBoxLayout()
        title = QLabel("Timing sweep"); title.setProperty("heading", True)
        head.addWidget(title); head.addStretch(1)
        # Mode is derived, not chosen: hardware iff at least one hardware
        # node is configured (see _current_mode / _sync_mode_ui), same as
        # the Test tab -- both tabs share the same hardware node list.
        self.mode_label = QLabel("")
        self.mode_label.setProperty("heading", True)
        head.addWidget(self.mode_label)
        self.hw_nodes_btn = QPushButton("Configure hardware nodes…")
        self.hw_nodes_btn.clicked.connect(self._open_nodes_dialog)
        head.addWidget(self.hw_nodes_btn)
        outer.addLayout(head)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # ---- Top: build + deploy + params ----
        top = QWidget()
        tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(12)

        # Cross-compile / deploy row
        cd_row = QHBoxLayout(); cd_row.setSpacing(12)

        build_box = QGroupBox("Cross-compile")
        bf = QFormLayout(build_box); bf.setContentsMargins(12, 18, 12, 12); bf.setSpacing(8)
        self.target_triple = QLineEdit(s.target_triple)
        self.target_triple.setPlaceholderText(
            "e.g. aarch64-unknown-linux-gnu (empty = host build via cargo, "
            "set = cross-compile via `cross`)"
        )
        self.binary_name = QLineEdit(s.binary_name)
        self.build_features = QLineEdit(s.build_features)
        self.build_features.setPlaceholderText("comma-separated, e.g. diagnostic")
        bf.addRow("Target triple", self.target_triple)
        bf.addRow("Binary name", self.binary_name)
        bf.addRow("Features", self.build_features)
        bf_btns = QHBoxLayout()
        self.build_btn = QPushButton("cargo build --release")
        self.build_btn.setProperty("accent", True)
        self.build_btn.clicked.connect(self._on_build)
        self.deploy_btn = QPushButton("Deploy to all hardware nodes")
        self.deploy_btn.clicked.connect(self._on_deploy)
        bf_btns.addWidget(self.build_btn); bf_btns.addWidget(self.deploy_btn)
        bf.addRow(bf_btns)
        cd_row.addWidget(build_box, 2)

        # Sweep params
        pbox = QGroupBox("Sweep parameters")
        pf = QFormLayout(pbox); pf.setContentsMargins(12, 18, 12, 12); pf.setSpacing(8)
        self.candidates = QLineEdit("20,15,12,10,8,6,5,4,3,2")
        self.candidates.setPlaceholderText("descending list of cycle_ms, comma-separated")
        self.nominal = QSpinBox(); self.nominal.setRange(2, 16); self.nominal.setValue(3)
        self.minimum = QSpinBox(); self.minimum.setRange(1, 16); self.minimum.setValue(2)
        self.measure_s = QDoubleSpinBox(); self.measure_s.setRange(1.0, 300.0); self.measure_s.setValue(8.0); self.measure_s.setSuffix(" s")
        self.setup_timeout_s = QDoubleSpinBox(); self.setup_timeout_s.setRange(1.0, 120.0); self.setup_timeout_s.setValue(15.0); self.setup_timeout_s.setSuffix(" s")
        self.max_overrun_frac = QDoubleSpinBox(); self.max_overrun_frac.setRange(0.0, 1.0); self.max_overrun_frac.setSingleStep(0.005); self.max_overrun_frac.setDecimals(3); self.max_overrun_frac.setValue(0.02)
        self.overrun_tolerance_pct = QDoubleSpinBox()
        self.overrun_tolerance_pct.setRange(0.1, 100.0)
        self.overrun_tolerance_pct.setSingleStep(0.5)
        self.overrun_tolerance_pct.setDecimals(1)
        self.overrun_tolerance_pct.setValue(5.0)
        self.overrun_tolerance_pct.setSuffix(" %")
        pf.addRow("cycle_ms candidates", self.candidates)
        pf.addRow("nominal", self.nominal)
        pf.addRow("minimum", self.minimum)
        pf.addRow("measure duration", self.measure_s)
        pf.addRow("operational timeout", self.setup_timeout_s)
        pf.addRow("max overrun fraction", self.max_overrun_frac)
        pf.addRow("overrun tolerance", self.overrun_tolerance_pct)
        cd_row.addWidget(pbox, 2)

        tl.addLayout(cd_row)

        # Sweep control row
        ctl = QHBoxLayout()
        self.start_btn = QPushButton("Start sweep")
        self.start_btn.setProperty("accent", True)
        self.start_btn.clicked.connect(self._on_start_sweep)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("danger", True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_sweep)
        self.smallest_lbl = QLabel(""); self.smallest_lbl.setProperty("muted", True)
        ctl.addWidget(self.start_btn); ctl.addWidget(self.cancel_btn)
        ctl.addWidget(self.smallest_lbl); ctl.addStretch(1)
        tl.addLayout(ctl)

        split.addWidget(top)

        # ---- Middle: results table ----
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.table.setFont(mono)
        split.addWidget(self.table)

        # ---- Bottom: log ----
        log_wrap = QWidget()
        lw = QVBoxLayout(log_wrap); lw.setContentsMargins(0, 8, 0, 0); lw.setSpacing(6)
        lh = QLabel("Log"); lh.setProperty("heading", True)
        lw.addWidget(lh)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(mono); self.log.setLineWrapMode(QTextEdit.NoWrap)
        self.log.setPlaceholderText("Build, deploy and sweep output appear here.")
        lw.addWidget(self.log)
        split.addWidget(log_wrap)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 3)
        outer.addWidget(split, 1)

        self._sync_mode_ui()

    # ---- mode toggle ------------------------------------------------------

    def _current_mode(self) -> str:
        """Mode is derived, not chosen: hardware iff at least one hardware
        node is both configured and enabled, simulated otherwise."""
        s = self._get_settings()
        return "hardware" if enabled_nodes(nodes_from_json(s.hardware_nodes_json)) else "simulated"

    def _sync_mode_ui(self) -> None:
        is_hw = self._current_mode() == "hardware"
        self.mode_label.setText(f"Mode: {self._current_mode()}")
        self.deploy_btn.setEnabled(is_hw)
        # In hardware mode "nominal" is derived from the enabled node
        # count (see _on_start_sweep), not manually set -- disable the
        # spinbox and show the derived value so it's not a stale/wrong
        # number sitting there unused.
        self.nominal.setEnabled(not is_hw)
        if is_hw:
            s = self._get_settings()
            count = len(enabled_nodes(nodes_from_json(s.hardware_nodes_json)))
            if count > 0:
                self.nominal.setValue(count)
            self.nominal.setToolTip("Derived from the enabled hardware node count.")
        else:
            self.nominal.setToolTip("")

    # ---- hardware nodes dialog -------------------------------------------

    def _open_nodes_dialog(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        dlg = HardwareNodesDialog(nodes, parent=self)
        if dlg.exec():
            updated = dlg.collect()
            from dataclasses import replace
            new = replace(s, hardware_nodes_json=nodes_to_json(updated))
            self._save_settings(new)
            self._append_build_line(f"[nodes] saved {len(updated)} hardware node(s)")
            self._sync_mode_ui()

    # ---- cross-compile ---------------------------------------------------

    def _on_build(self) -> None:
        s = self._get_settings()
        if not s.rust_repo_path:
            QMessageBox.warning(self, "No repo",
                                "Set 'Rust repository' in Settings first.")
            return
        # persist current build fields
        from dataclasses import replace
        self._save_settings(replace(
            s,
            target_triple=self.target_triple.text().strip(),
            binary_name=self.binary_name.text().strip() or "node",
            build_features=self.build_features.text().strip(),
        ))
        s = self._get_settings()

        features = [f.strip() for f in s.build_features.split(",") if f.strip()]
        self._build_helper = CrossBuild(
            repo_path=Path(s.rust_repo_path),
            target_triple=s.target_triple,
            features=features,
            binary_name=s.binary_name,
        )
        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building…")
        self._built_binary_path = None
        self.log.clear()

        self._build_helper.start(
            on_line=lambda ln: self._build_line.emit(ln),
            on_done=lambda rc, path: self._build_done.emit(rc, path),
        )

    @Slot(int, object)
    def _on_build_done(self, rc: int, path: Optional[Path]) -> None:
        self.build_btn.setEnabled(True)
        self.build_btn.setText("cargo build --release")
        self._built_binary_path = path

    # ---- deploy binary to hardware ---------------------------------------

    def _on_deploy(self) -> None:
        s = self._get_settings()
        nodes = enabled_nodes(nodes_from_json(s.hardware_nodes_json))
        if not nodes:
            QMessageBox.warning(self, "No nodes",
                                "Add and enable hardware nodes first (Configure hardware nodes…).")
            return
        if not s.rust_repo_path:
            QMessageBox.warning(self, "No repo",
                                "Set 'Rust repository' in Settings first.")
            return

        self.deploy_btn.setEnabled(False)
        self.deploy_btn.setText("Deploying…")
        self.log.clear()
        self._append_build_line(f"=== deploy: {len(nodes)} enabled node(s) ===")

        def worker() -> None:
            try:
                bin_path = self._build_and_push_binary(nodes, s, self._deploy_line.emit)
                self._deploy_done.emit(bin_path is not None)
            except Exception as e:
                # Safety net: an unhandled exception (anything other
                # than SshError, which _build_and_push_binary already
                # catches per-node) used to kill this thread silently --
                # _deploy_done never fired, deploy_btn stayed stuck on
                # "Deploying…" forever with no visible error.
                self._deploy_line.emit(f"[deploy] unexpected {type(e).__name__}: {e}")
                self._deploy_done.emit(False)

        threading.Thread(target=worker, name="deploy", daemon=True).start()

    def _build_and_push_binary(self, nodes: list[HardwareNode], s,
                                emit_line) -> Optional[Path]:
        """Ensure a built binary exists locally (building with `cross` +
        the configured features if it's missing) and push it to every
        node in `nodes`. Returns the local binary path on success, None
        on any failure. Runs synchronously -- call from a worker thread.
        Shared by the "Deploy to all hardware nodes" button and the
        hardware-mode sweep, which both need the same "binary is
        actually on the node before we try to start it" guarantee.
        """
        features = [f.strip() for f in (s.build_features or "").split(",") if f.strip()]
        helper = CrossBuild(
            repo_path=Path(s.rust_repo_path),
            target_triple=s.target_triple,
            features=features,
            binary_name=s.binary_name or "node",
        )
        # Prefer the binary from the last in-tab build if it's still
        # there; otherwise build it now instead of just complaining
        # it's missing -- with the configured features (defaults to
        # "diagnostic", required for the JSON diag channel).
        bin_path = self._built_binary_path
        if bin_path is None or not bin_path.exists():
            expected = helper.expected_binary_path()
            if expected.exists():
                bin_path = expected
            else:
                emit_line(
                    f"[deploy] no binary at {expected} -- building first "
                    f"(features={s.build_features!r})..."
                )
                rc, built = self._blocking_build(helper)
                if rc != 0 or built is None:
                    emit_line("[deploy] aborted: build failed")
                    return None
                bin_path = built

        emit_line(f"[deploy] source: {bin_path}")
        ok_all = True
        for hn in nodes:
            try:
                emit_line(f"[deploy] {hn.host} → {hn.remote_binary}")
                scp_file(hn, bin_path, hn.remote_binary, timeout=120.0)
                emit_line(f"[deploy] {hn.host} ok")
            except SshError as e:
                ok_all = False
                emit_line(f"[deploy] {hn.host} FAILED: {e.output}")
        return bin_path if ok_all else None

    def _blocking_build(self, cross: CrossBuild) -> tuple[int, Optional[Path]]:
        """Run CrossBuild synchronously from within the deploy worker
        thread, streaming its output into the same log the deploy
        itself writes to."""
        done = threading.Event()
        result: dict = {"rc": -1, "path": None}

        def on_done(rc: int, path: Optional[Path]) -> None:
            result["rc"] = rc
            result["path"] = path
            done.set()

        cross.start(on_line=lambda ln: self._deploy_line.emit(ln), on_done=on_done)
        done.wait()
        return result["rc"], result["path"]

    @Slot(bool)
    def _on_deploy_done(self, ok: bool) -> None:
        self.deploy_btn.setEnabled(True)
        self.deploy_btn.setText("Deploy to all hardware nodes")
        self._append_build_line("[deploy] done" + ("" if ok else " (with errors)"))

    # ---- sweep -----------------------------------------------------------

    def _on_start_sweep(self) -> None:
        s = self._get_settings()
        # parse candidates
        try:
            cands = [int(x.strip()) for x in self.candidates.text().split(",") if x.strip()]
        except ValueError:
            QMessageBox.warning(self, "Bad candidates",
                                "cycle_ms candidates must be comma-separated integers.")
            return
        if not cands:
            return
        if not s.rust_repo_path:
            QMessageBox.warning(self, "No repo",
                                "Set 'Rust repository' in Settings first.")
            return

        mode = self._current_mode()
        hw_nodes: list[HardwareNode] = []
        if mode == "hardware":
            hw_nodes = enabled_nodes(nodes_from_json(s.hardware_nodes_json))
            if not hw_nodes:
                QMessageBox.warning(self, "No nodes",
                                    "Add and enable hardware nodes first.")
                return
            if self.minimum.value() > len(hw_nodes):
                QMessageBox.warning(
                    self, "Bad quorum",
                    f"'minimum' ({self.minimum.value()}) can't exceed the "
                    f"number of enabled hardware nodes ({len(hw_nodes)}).",
                )
                return

        # Reset UI state -- predeploy (build if missing, push for
        # hardware) happens in a worker thread before the sweep itself
        # starts, same "don't just complain the binary is missing"
        # guarantee as the Deploy button. cancel_btn stays disabled
        # until there's an actual sweep running to cancel.
        self.table.setRowCount(0)
        self.smallest_lbl.setText("")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._append_sweep_line(
            f"\n=== sweep start ({mode}, {len(cands)} candidates) ==="
        )

        def predeploy_and_build_params() -> None:
            try:
                if mode == "simulated":
                    helper = CrossBuild(
                        repo_path=Path(s.rust_repo_path),
                        target_triple="",  # host build for simulated
                        binary_name=s.binary_name or "node",
                    )
                    bin_path = helper.expected_binary_path()
                    if not bin_path.exists():
                        self._sweep_line.emit(
                            f"[build] host binary missing at {bin_path} -- building..."
                        )
                        rc, built = self._blocking_build(helper)
                        if rc != 0 or built is None:
                            self._sweep_line.emit("[build] aborted: build failed")
                            self._predeploy_failed.emit()
                            return
                        bin_path = built
                    work_dir = Path(s.session_log_dir) / "timing_sweep"
                    work_dir.mkdir(parents=True, exist_ok=True)
                    params = SweepParams(
                        candidates_ms=cands,
                        nominal=self.nominal.value(),
                        minimum=self.minimum.value(),
                        measure_s=self.measure_s.value(),
                        setup_timeout_s=self.setup_timeout_s.value(),
                        max_overrun_fraction=self.max_overrun_frac.value(),
                        overrun_tolerance_pct=self.overrun_tolerance_pct.value(),
                        diag_group=s.diag_group, diag_port=s.diag_port,
                        op_group=s.op_group, op_port=s.op_port,
                        interface_ip=s.interface_ip,
                        local_binary=bin_path,
                        local_work_dir=work_dir,
                    )
                else:
                    bin_path = self._build_and_push_binary(hw_nodes, s, self._sweep_line.emit)
                    if bin_path is None:
                        self._predeploy_failed.emit()
                        return
                    params = SweepParams(
                        candidates_ms=cands,
                        # Derived from the enabled hardware nodes, not the
                        # spinbox -- a manually-set nominal that drifts out
                        # of sync with which nodes are actually enabled was
                        # exactly the "always blasts out 2oo3 configs" bug.
                        nominal=len(hw_nodes),
                        minimum=self.minimum.value(),
                        measure_s=self.measure_s.value(),
                        setup_timeout_s=self.setup_timeout_s.value(),
                        max_overrun_fraction=self.max_overrun_frac.value(),
                        overrun_tolerance_pct=self.overrun_tolerance_pct.value(),
                        diag_group=s.diag_group, diag_port=s.diag_port,
                        op_group=s.op_group, op_port=s.op_port,
                        interface_ip=s.interface_ip,
                        hardware_nodes=hw_nodes,
                        node_interface=s.node_network_interface,
                    )
                self._sweep_ready.emit(mode, params)
            except Exception as e:
                # Safety net -- see the equivalent fix in _on_deploy's
                # worker: without this, any unexpected error here left
                # the Start button stuck disabled forever, with neither
                # _sweep_ready nor _predeploy_failed ever firing.
                self._sweep_line.emit(f"[build] unexpected {type(e).__name__}: {e}")
                self._predeploy_failed.emit()

        threading.Thread(
            target=predeploy_and_build_params, name="sweep-predeploy", daemon=True,
        ).start()

    @Slot()
    def _on_predeploy_failed(self) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    @Slot(str, object)
    def _on_sweep_ready(self, mode: str, params) -> None:
        self._sweep = TimingSweep(
            mode=mode, params=params,
            on_line=lambda ln: self._sweep_line.emit(ln),
            on_result=lambda r: self._sweep_result.emit(r),
            on_done=lambda smallest: self._sweep_done.emit(smallest),
        )
        self.cancel_btn.setEnabled(True)
        self._sweep.start()

    def _on_cancel_sweep(self) -> None:
        if self._sweep:
            self._sweep.cancel()
            self._append_sweep_line("[cancel requested]")

    @Slot(object)
    def _append_result_row(self, res: CandidateResult) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        cells = [
            str(res.cycle_ms),
            "yes" if res.operational else "no",
            str(res.n_cycles),
            str(res.mean_us),
            f"{res.overrun_frac:.2%}",
            str(res.worst_overrun_us),
            res.verdict,
            res.detail,
        ]
        color = VERDICT_COLORS.get(res.verdict, QColor("#DBDEE1"))
        for col, txt in enumerate(cells):
            item = QTableWidgetItem(txt)
            item.setForeground(color)
            if col == 6:
                f = item.font(); f.setBold(True); item.setFont(f)
            self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    @Slot(object)
    def _on_sweep_done(self, smallest) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if smallest is None:
            self.smallest_lbl.setText("No candidate was stable.")
        else:
            hz = 1000.0 / smallest
            self.smallest_lbl.setText(
                f"Smallest stable cycle: {smallest} ms  ({hz:.1f} Hz)"
            )
        self._append_sweep_line("=== sweep done ===")

    # ---- log helpers ------------------------------------------------------

    @Slot(str)
    def _append_build_line(self, line: str) -> None:
        self.log.append(line)
        c = self.log.textCursor(); c.movePosition(QTextCursor.End)
        self.log.setTextCursor(c)

    @Slot(str)
    def _append_sweep_line(self, line: str) -> None:
        self.log.append(line)
        c = self.log.textCursor(); c.movePosition(QTextCursor.End)
        self.log.setTextCursor(c)
