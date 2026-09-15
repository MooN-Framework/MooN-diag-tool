"""
Timing tab.

Three vertical sections:
  1. Sweep parameters (candidates, nominal/minimum, stability wait) +
     sweep/deploy controls.
  2. Live results table with colour-coded verdicts.
  3. Build/deploy/sweep log.

Cross-compile settings (target triple, binary name, features) live in
the Settings tab now, not here -- this tab only reads them. There's
also no standalone "build" action left in this tab: both "Deploy to
all hardware nodes" and "Start sweep" already build a missing binary
on demand (see _build_and_push_binary / predeploy_and_build_params),
so a separate manual build button was pure redundancy.

Finds the smallest candidate cycle_ms the full system still stays
healthy at: bring the candidate up, wait `stability_wait_s` doing
nothing, then ask every node once more whether it's still in a normal
operating state. That's the whole stability criterion.

Modes:
  simulated — spawn local Rust processes with generated TOML configs.
  hardware  — SSH-restart already-installed nodes with new configs.

All heavy work happens in worker threads; UI updates go through Qt
signals.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
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
from ..core.mode import derive_mode
from ..core.ssh_deploy import (
    HardwareNode,
    SshError,
    enabled_nodes,
    ensure_remote_dirs,
    nodes_from_json,
    nodes_to_json,
    scp_file,
    ssh_exec,
    stop_moon_node_service,
)
from ..core.timing_sweep import (
    CandidateResult,
    SweepParams,
    TimingSweep,
    VERDICT_NO_OP,
    VERDICT_NODE_DIED,
    VERDICT_NOT_STABLE,
    VERDICT_STABLE,
    VERDICT_EXCEPTION,
)
from .hardware_nodes_dialog import HardwareNodesDialog


HEADERS = ["cycle_ms", "operational", "still healthy after wait", "verdict", "detail"]

VERDICT_COLORS = {
    VERDICT_STABLE:     QColor("#23A55A"),
    VERDICT_NOT_STABLE: QColor("#F0B232"),
    VERDICT_NO_OP:      QColor("#DA373C"),
    VERDICT_NODE_DIED:  QColor("#DA373C"),
    VERDICT_EXCEPTION:  QColor("#DA373C"),
}


class TimingTab(QWidget):
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
            hardware node list)
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._save_settings = settings_saver
        self._sweep: Optional[TimingSweep] = None

        self._build_ui()

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

        # ---- Top: params + controls ----
        top = QWidget()
        tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(12)

        # Sweep params -- capped to a fraction of the row width (plus a
        # trailing stretch) rather than left to fill it on its own, so
        # the form doesn't stretch its labels/fields absurdly wide on
        # an ultra-wide window now that it's no longer sharing this row
        # with the (now-removed) Cross-compile box.
        params_row = QHBoxLayout(); params_row.setSpacing(12)
        pbox = QGroupBox("Sweep parameters")
        pf = QFormLayout(pbox); pf.setContentsMargins(12, 18, 12, 12); pf.setSpacing(8)
        self.candidates = QLineEdit("20,15,12,10,8,6")
        self.candidates.setPlaceholderText("descending list of cycle_ms, comma-separated")
        self.nominal = QSpinBox(); self.nominal.setRange(2, 16); self.nominal.setValue(3)
        self.minimum = QSpinBox(); self.minimum.setRange(1, 16); self.minimum.setValue(2)
        self.stability_wait_s = QDoubleSpinBox()
        self.stability_wait_s.setRange(1.0, 300.0)
        self.stability_wait_s.setValue(8.0)
        self.stability_wait_s.setSuffix(" s")
        self.stability_wait_s.setToolTip(
            "After the candidate comes up, wait this long doing nothing, "
            "then check once more that every node is still in a normal "
            "operating state (not Startup/InitSync/Isolation/Failsafe/"
            "ErrorManagement). That's the whole stability check -- is the "
            "full system still up after waiting."
        )
        self.setup_timeout_s = QDoubleSpinBox(); self.setup_timeout_s.setRange(1.0, 120.0); self.setup_timeout_s.setValue(15.0); self.setup_timeout_s.setSuffix(" s")
        pf.addRow("cycle_ms candidates", self.candidates)
        pf.addRow("nominal", self.nominal)
        pf.addRow("minimum", self.minimum)
        pf.addRow("stability wait", self.stability_wait_s)
        pf.addRow("operational timeout", self.setup_timeout_s)
        params_row.addWidget(pbox, 2)
        params_row.addStretch(1)
        tl.addLayout(params_row)

        # Sweep control row
        ctl = QHBoxLayout()
        self.start_btn = QPushButton("Start sweep")
        self.start_btn.setProperty("accent", True)
        self.start_btn.clicked.connect(self._on_start_sweep)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("danger", True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_sweep)
        # Nothing on disk records which cargo features an existing
        # binary was built with, so "the file is there" is not the same
        # as "the file is usable". This is the manual override for that:
        # a stale build without the diagnostic feature produces nodes
        # that run fine and never answer on the diag channel, and the
        # only way out used to be deleting the file by hand.
        self.force_rebuild = QCheckBox("Force rebuild")
        self.force_rebuild.setToolTip(
            "Rebuild the node binary even if one already exists at the "
            "expected path. Use this after changing Settings > Build > "
            "Features, or whenever the sweep reports that no node "
            "answered on the diag channel."
        )
        # Builds a missing binary itself before pushing (see
        # _build_and_push_binary) -- no separate "build" step needed.
        self.deploy_btn = QPushButton("Deploy to all hardware nodes")
        self.deploy_btn.clicked.connect(self._on_deploy)
        self.smallest_lbl = QLabel(""); self.smallest_lbl.setProperty("muted", True)
        ctl.addWidget(self.start_btn); ctl.addWidget(self.cancel_btn)
        ctl.addWidget(self.deploy_btn)
        ctl.addWidget(self.force_rebuild)
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
        """See core.mode.derive_mode -- shared with the Test and
        Settings tabs so all three agree on what mode is active."""
        return derive_mode(self._get_settings())

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

    def on_settings_changed(self, _settings) -> None:
        """Connected to Bus.settings_changed in main_window -- fires on
        every settings apply, from this tab's own hardware-nodes
        dialog, the Test/Package tab's, or the Settings tab's Apply
        button (which is now the only place target_triple/binary_name/
        build_features are edited). Keeps the mode label and derived
        nominal here in sync without needing a full app restart or a
        manual click in this tab."""
        self._sync_mode_ui()

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
        if not s.target_triple:
            self._append_build_line(
                "[deploy] WARNING: no target triple set (Settings → Build "
                "(hardware mode)) -- building a host binary, which won't "
                "run on the hardware nodes' architecture unless it "
                "happens to match this machine's."
            )

        # Read on the GUI thread; the worker below must not touch widgets.
        force_rebuild = self.force_rebuild.isChecked()

        self.deploy_btn.setEnabled(False)
        self.deploy_btn.setText("Deploying…")
        self.log.clear()
        self._append_build_line(f"=== deploy: {len(nodes)} enabled node(s) ===")

        def worker() -> None:
            try:
                bin_path = self._build_and_push_binary(
                    nodes, s, force_rebuild, self._deploy_line.emit,
                )
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
                               force_rebuild: bool,
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
        # No standalone "build" button/action anymore -- always check
        # the expected on-disk path first, building fresh only if it's
        # missing (with the configured features, defaults to
        # "diagnostic", required for the JSON diag channel).
        expected = helper.expected_binary_path()
        if expected.exists() and not force_rebuild:
            bin_path = expected
            emit_line(
                f"[deploy] reusing existing binary at {expected} "
                f"(expected features={s.build_features!r}; tick "
                "'Force rebuild' to build it again)"
            )
        else:
            why = ("rebuild forced" if expected.exists()
                   else f"no binary at {expected}")
            emit_line(
                f"[deploy] {why} -- building first "
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
                # Preparation phase, same reasoning as TimingSweep._hw_start /
                # TestTab._deploy_to_hardware: stop the production
                # moon-node.service (best-effort -- it may not exist on a
                # node that was never given a package), AND kill any
                # already-running instance of the harness's OWN
                # nohup-started process (resolved_stop_cmd()) -- without
                # this second one, a node this tool already deployed to
                # and started earlier keeps that binary running, and scp
                # trying to overwrite a currently-executing file fails
                # with ETXTBSY ("text file busy", shows up as a generic
                # "dest open ... Failure"). Then make sure the destination
                # directory actually exists -- scp can't create missing
                # parent dirs, and /opt/moon/bin only exists after
                # moon-pkg-load.service has extracted a package into that
                # boot's tmpfs.
                emit_line(f"[deploy] {hn.host} stopping moon-node.service (if present)")
                stop_moon_node_service(hn)
                try:
                    ssh_exec(hn, hn.resolved_stop_cmd(), timeout=30.0)
                except SshError as e:
                    emit_line(f"[deploy] {hn.host} stop_cmd failed (continuing): {e.output}")
                time.sleep(0.3)  # let the old process actually exit before we overwrite its binary
                ensure_remote_dirs(hn)
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
        # Read on the GUI thread: the worker below must not touch widgets.
        force_rebuild = self.force_rebuild.isChecked()
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
                    # The configured features MUST be passed here too,
                    # not just on the hardware path. The sweep drives
                    # every node exclusively over the JSON diag channel
                    # (_wait_operational and _probe_states both call
                    # get_status), and that channel only exists when the
                    # binary was built with the "diagnostic" feature.
                    # Built without it, the nodes come up perfectly fine
                    # and simply never answer, so every candidate came
                    # back NO_OPERATIONAL with nothing in the log
                    # pointing at the cause.
                    features = [
                        f.strip() for f in (s.build_features or "").split(",")
                        if f.strip()
                    ]
                    helper = CrossBuild(
                        repo_path=Path(s.rust_repo_path),
                        target_triple="",  # host build for simulated
                        features=features,
                        binary_name=s.binary_name or "node",
                    )
                    bin_path = helper.expected_binary_path()
                    if force_rebuild or not bin_path.exists():
                        why = ("rebuild forced" if bin_path.exists()
                               else f"host binary missing at {bin_path}")
                        self._sweep_line.emit(
                            f"[build] {why} -- "
                            f"building (features={s.build_features!r})..."
                        )
                        rc, built = self._blocking_build(helper)
                        if rc != 0 or built is None:
                            self._sweep_line.emit("[build] aborted: build failed")
                            self._predeploy_failed.emit()
                            return
                        bin_path = built
                    else:
                        # An existing binary is reused as-is, and nothing
                        # on disk records which features it was built
                        # with. Say so, so a stale non-diagnostic build
                        # is at least visible in the log.
                        self._sweep_line.emit(
                            f"[build] reusing existing host binary at {bin_path} "
                            f"(expected features={s.build_features!r}; tick "
                            "'Force rebuild' to build it again)"
                        )
                    work_dir = Path(s.session_log_dir) / "timing_sweep"
                    work_dir.mkdir(parents=True, exist_ok=True)
                    params = SweepParams(
                        candidates_ms=cands,
                        nominal=self.nominal.value(),
                        minimum=self.minimum.value(),
                        stability_wait_s=self.stability_wait_s.value(),
                        setup_timeout_s=self.setup_timeout_s.value(),
                        diag_group=s.diag_group, diag_port=s.diag_port,
                        op_group=s.op_group, op_port=s.op_port,
                        interface_ip=s.interface_ip,
                        local_binary=bin_path,
                        local_work_dir=work_dir,
                    )
                else:
                    bin_path = self._build_and_push_binary(
                        hw_nodes, s, force_rebuild, self._sweep_line.emit,
                    )
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
                        stability_wait_s=self.stability_wait_s.value(),
                        setup_timeout_s=self.setup_timeout_s.value(),
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
            "yes" if res.still_healthy_after_wait else "no",
            res.verdict,
            res.detail,
        ]
        color = VERDICT_COLORS.get(res.verdict, QColor("#DBDEE1"))
        for col, txt in enumerate(cells):
            item = QTableWidgetItem(txt)
            item.setForeground(color)
            if col == 3:
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
