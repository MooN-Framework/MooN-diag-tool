"""
Package tab: build a signed .moonpkg (see core/moon_package.py for the
exact format) and optionally deploy it straight to a configured
hardware node.

Two independent actions, both usable on their own:
- "Build package" cross-compiles the prod binary (default features --
  NOT "diagnostic", that's for the debug image) with the target triple
  from Settings, renders a node.toml for the chosen node_id/topology,
  and signs the result via tools/build-moon-package.sh from the pi-gen
  repo (see Settings: "pi-gen repository" / "Package signing key").
- "Deploy to node" pushes an already-built (or just-built) .moonpkg to
  /boot/firmware/moon-package.moonpkg on the selected node and
  restarts moon-pkg-load.service + moon-node.service, mirroring
  tools/moon-deploy.sh's live-reload path (--pkg without --reboot).

Only the signed/prod flow is supported -- there's no "build unsigned"
button, matching the decision to keep this tool's packaging output
always production-signed.
"""
from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.cross_compile import CrossBuild
from ..core.moon_package import PackageBuildError, build_package, build_payload_dir
from ..core.ssh_deploy import (
    HardwareNode,
    SshError,
    nodes_from_json,
    scp_bytes,
    scp_file,
    ssh_exec,
    wait_for_reachable,
)
from .hardware_nodes_dialog import HardwareNodesDialog

from harness.config_gen import make_spec, render_toml_str

_REMOTE_PKG_PATH = "/boot/firmware/moon-package.moonpkg"  # fixed, matches moon-deploy.sh


class PackageTab(QWidget):
    line_received = Signal(str)
    _build_done = Signal(bool, str)   # ok, package path or error
    _deploy_done = Signal(bool)

    def __init__(self, settings_provider, settings_saver, parent=None) -> None:
        """
        settings_provider() -> current AppSettings
        settings_saver(new_settings) -> persists changes (shared hardware
            node list, same pattern as Test/Timing tabs)
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._save_settings = settings_saver
        self._last_built_package: Optional[Path] = None

        self._build_ui()
        self.line_received.connect(self._append_line)
        self._build_done.connect(self._on_build_done)
        self._deploy_done.connect(self._on_deploy_done)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20); outer.setSpacing(14)

        title = QLabel("Package"); title.setProperty("heading", True)
        outer.addWidget(title)

        split = QSplitter(Qt.Vertical)

        top = QWidget()
        tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(14)

        # --- Build group ---
        build_box = QGroupBox("Build package (signed, prod)")
        bf = QFormLayout(build_box); bf.setContentsMargins(12, 20, 12, 12); bf.setSpacing(10)

        s = self._get_settings()
        self.pkg_name = QLineEdit("moon-node")
        self.pkg_version = QLineEdit(datetime.now(timezone.utc).strftime("%Y.%m.%d-%H%M"))
        self.node_id = QSpinBox(); self.node_id.setRange(0, 15); self.node_id.setValue(0)
        self.nominal = QSpinBox(); self.nominal.setRange(2, 16); self.nominal.setValue(3)
        self.minimum = QSpinBox(); self.minimum.setRange(1, 16); self.minimum.setValue(2)
        self.cycle_ms = QSpinBox(); self.cycle_ms.setRange(1, 1000); self.cycle_ms.setValue(20)
        self.init_sync_timeout = QSpinBox(); self.init_sync_timeout.setRange(1, 600_000)
        self.init_sync_timeout.setValue(15_000); self.init_sync_timeout.setSuffix(" ms")
        self.init_sync_timeout.setToolTip(
            "How long a node waits at startup for its peers before giving "
            "up. Defaults to 15000 ms here since this tab targets real "
            "hardware -- nodes power up at different times and boot "
            "slower than a local/simulated run."
        )
        self.diag_group = QLineEdit(s.diag_group)
        self.diag_port = QSpinBox(); self.diag_port.setRange(1, 65535); self.diag_port.setValue(s.diag_port)
        self.op_group = QLineEdit(s.op_group)
        self.op_port = QSpinBox(); self.op_port.setRange(1, 65535); self.op_port.setValue(s.op_port)
        self.node_iface = QLineEdit(s.node_network_interface)
        self.features = QLineEdit("")
        self.features.setPlaceholderText("empty = prod default (no 'diagnostic')")
        self.features.setToolTip(
            "Cargo features for the packaged binary. Leave empty for a "
            "prod build -- 'diagnostic' is what makes the debug image's "
            "binary debug, packaging it here defeats the prod/debug split."
        )
        self.out_path = QLineEdit(self._default_out_path())
        out_row = QWidget()
        oh = QHBoxLayout(out_row); oh.setContentsMargins(0, 0, 0, 0); oh.setSpacing(6)
        oh.addWidget(self.out_path)
        browse_btn = QPushButton("Save as…"); browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._on_browse_out_path)
        oh.addWidget(browse_btn)

        bf.addRow("Name", self.pkg_name)
        bf.addRow("Version", self.pkg_version)
        bf.addRow("Node ID", self.node_id)
        bf.addRow("Nominal / Minimum", self._hbox(self.nominal, self.minimum))
        bf.addRow("Cycle duration (ms)", self.cycle_ms)
        bf.addRow("Init sync timeout (ms)", self.init_sync_timeout)
        bf.addRow("Diag group / port", self._hbox(self.diag_group, self.diag_port))
        bf.addRow("Op group / port", self._hbox(self.op_group, self.op_port))
        bf.addRow("Node network interface", self.node_iface)
        bf.addRow("Build features", self.features)
        bf.addRow("Output file", out_row)

        self.build_btn = QPushButton("Build package")
        self.build_btn.setProperty("accent", True)
        self.build_btn.clicked.connect(self._on_build)
        bf.addRow(self.build_btn)
        tl.addWidget(build_box)

        # --- Deploy group ---
        deploy_box = QGroupBox("Deploy to node")
        df = QFormLayout(deploy_box); df.setContentsMargins(12, 20, 12, 12); df.setSpacing(10)

        pkg_row = QWidget()
        ph = QHBoxLayout(pkg_row); ph.setContentsMargins(0, 0, 0, 0); ph.setSpacing(6)
        self.deploy_pkg_path = QLineEdit("")
        self.deploy_pkg_path.setPlaceholderText("filled in after a build, or pick an existing .moonpkg")
        ph.addWidget(self.deploy_pkg_path)
        deploy_browse_btn = QPushButton("…"); deploy_browse_btn.setFixedWidth(36)
        deploy_browse_btn.clicked.connect(self._on_browse_deploy_pkg)
        ph.addWidget(deploy_browse_btn)

        node_row = QWidget()
        nh = QHBoxLayout(node_row); nh.setContentsMargins(0, 0, 0, 0); nh.setSpacing(6)
        self.target_node = QComboBox()
        nh.addWidget(self.target_node, 1)
        nodes_btn = QPushButton("Configure hardware nodes…")
        nodes_btn.clicked.connect(self._open_nodes_dialog)
        nh.addWidget(nodes_btn)

        df.addRow("Package file", pkg_row)
        df.addRow("Target node", node_row)

        # Deliberately separate from the package itself -- matches
        # moon-deploy.sh: static IP lives in its own systemd-networkd
        # file outside the signed integrity manifest, not in the
        # .moonpkg. Off by default, same as that script requiring an
        # explicit --ip.
        self.set_ip_chk = QCheckBox("Also set static IP")
        self.set_ip_chk.toggled.connect(self._on_set_ip_toggled)
        df.addRow(self.set_ip_chk)
        self.new_ip = QLineEdit(); self.new_ip.setPlaceholderText("e.g. 192.168.1.2/24")
        self.gateway = QLineEdit(); self.gateway.setPlaceholderText("e.g. 192.168.1.1 (optional)")
        self.dns = QLineEdit(); self.dns.setPlaceholderText("e.g. 192.168.1.1 (optional)")
        self.net_iface = QLineEdit(s.node_network_interface)
        df.addRow("New address (CIDR)", self.new_ip)
        df.addRow("Gateway", self.gateway)
        df.addRow("DNS", self.dns)
        df.addRow("Interface", self.net_iface)
        self.reboot_chk = QCheckBox("Reboot after deploy")
        self.reboot_chk.setToolTip(
            "A new IP is only written to disk, never applied live -- "
            "restarting systemd-networkd over the same session you're "
            "connected through can hang the connection if the new "
            "address isn't reachable the same way. Check this to reboot "
            "into it instead; you'll need to reconnect at the new "
            "address afterwards."
        )
        df.addRow(self.reboot_chk)
        self._on_set_ip_toggled(False)

        self.deploy_btn = QPushButton(f"Push to {_REMOTE_PKG_PATH} + restart")
        self.deploy_btn.clicked.connect(self._on_deploy)
        df.addRow(self.deploy_btn)
        tl.addWidget(deploy_box)
        tl.addStretch(1)

        # Wrap in a QScrollArea instead of adding `top` to the splitter
        # directly: a QSplitter is happy to compress a child below its
        # sizeHint when space is tight, which was squashing every row
        # in these two forms short enough to clip the text inside the
        # QLineEdits. A scroll area keeps `top` at its natural height
        # and scrolls instead of shrinking it.
        top_scroll = QScrollArea()
        top_scroll.setWidget(top)
        top_scroll.setWidgetResizable(True)
        top_scroll.setFrameShape(QScrollArea.NoFrame)
        split.addWidget(top_scroll)

        # --- Log ---
        bottom = QWidget()
        bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(6)
        bl.addWidget(QLabel("Log"))
        self.log = QTextEdit(); self.log.setReadOnly(True)
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)
        self.log.setLineWrapMode(QTextEdit.NoWrap)
        bl.addWidget(self.log)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        self._refresh_node_combo()

    @staticmethod
    def _hbox(*widgets: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        for wi in widgets:
            h.addWidget(wi)
        return w

    def _default_out_path(self) -> str:
        s = self._get_settings()
        return str(Path(s.session_log_dir) / "packages" / "moon-node.moonpkg")

    # ---- hardware node list (shared with Test/Timing tabs) -----------------

    def _refresh_node_combo(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        self.target_node.clear()
        for n in sorted(nodes, key=lambda n: n.node_id):
            tag = "enabled" if n.enabled else "disabled"
            self.target_node.addItem(f"{n.node_id}: {n.host} ({tag})", userData=n.node_id)
        self.deploy_btn.setEnabled(bool(nodes))
        if not nodes:
            self.target_node.addItem("(none configured)", userData=None)

    def _open_nodes_dialog(self) -> None:
        s = self._get_settings()
        nodes = nodes_from_json(s.hardware_nodes_json)
        dlg = HardwareNodesDialog(nodes, parent=self)
        if dlg.exec():
            from ..core.ssh_deploy import nodes_to_json
            updated = dlg.collect()
            new = replace(s, hardware_nodes_json=nodes_to_json(updated))
            self._save_settings(new)
            self._append_line(f"[nodes] saved {len(updated)} hardware node(s)")
            self._refresh_node_combo()

    def on_settings_changed(self, _settings) -> None:
        """Connected to Bus.settings_changed in main_window -- fires on
        every settings apply, not just this tab's own hardware-nodes
        dialog save. Without this, editing hardware nodes from the
        Test or Timing tab left this tab's target-node dropdown stale
        until something in this tab happened to refresh it."""
        self._refresh_node_combo()

    # ---- build ---------------------------------------------------------

    def _on_set_ip_toggled(self, checked: bool) -> None:
        for w in (self.new_ip, self.gateway, self.dns, self.net_iface):
            w.setEnabled(checked)

    def _on_browse_out_path(self) -> None:
        p, _ = QFileDialog.getSaveFileName(
            self, "Save package as", self.out_path.text() or self._default_out_path(),
            "MooN package (*.moonpkg)",
        )
        if p:
            self.out_path.setText(p)

    def _on_build(self) -> None:
        s = self._get_settings()
        if not s.rust_repo_path:
            QMessageBox.warning(self, "No repo", "Set 'Rust repository' in Settings first.")
            return
        if not s.moon_pi_gen_repo_path:
            QMessageBox.warning(self, "No pi-gen repo",
                                "Set 'pi-gen repository' in Settings first "
                                "(needed for tools/build-moon-package.sh).")
            return
        if not s.moon_signing_key_path:
            QMessageBox.warning(self, "No signing key",
                                "Set 'Package signing key' in Settings first.")
            return
        name = self.pkg_name.text().strip()
        version = self.pkg_version.text().strip()
        out_path = Path(self.out_path.text().strip() or self._default_out_path())
        if not name or not version:
            QMessageBox.warning(self, "Missing fields", "Name and version are required.")
            return

        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building…")
        self.log.clear()
        self._append_line(f"=== build {name} v{version} (node_id={self.node_id.value()}) ===")

        features = [f.strip() for f in self.features.text().split(",") if f.strip()]
        cross = CrossBuild(
            repo_path=Path(s.rust_repo_path),
            target_triple=s.target_triple,
            features=features,
            binary_name=s.binary_name or "node",
        )
        spec_kwargs = dict(
            own_id=self.node_id.value(),
            nominal=self.nominal.value(), minimum=self.minimum.value(),
            cycle_ms=self.cycle_ms.value(),
            fabric_group=self.op_group.text().strip(), fabric_port=self.op_port.value(),
            diag_group=self.diag_group.text().strip(), diag_port=self.diag_port.value(),
            interface=self.node_iface.text().strip() or "eth0",
            init_sync_timeout_ms=self.init_sync_timeout.value(),
        )
        pi_gen_repo = Path(s.moon_pi_gen_repo_path)
        signing_key = Path(s.moon_signing_key_path)

        def worker() -> None:
            try:
                done = threading.Event()
                result: dict = {"rc": -1, "path": None}

                def on_done(rc: int, path: Optional[Path]) -> None:
                    result["rc"] = rc; result["path"] = path
                    done.set()

                cross.start(on_line=self.line_received.emit, on_done=on_done)
                done.wait()
                if result["rc"] != 0 or result["path"] is None:
                    self._build_done.emit(False, "build failed, see log above")
                    return

                bin_path = result["path"]
                spec = make_spec(**spec_kwargs)
                toml_text = render_toml_str(spec)
                workdir = Path(tempfile.mkdtemp(prefix="diag-tool-pkg-"))
                payload_dir = build_payload_dir(bin_path, toml_text, workdir)
                self.line_received.emit(f"[package] payload → {payload_dir}")
                build_package(
                    pi_gen_repo=pi_gen_repo, signing_key=signing_key,
                    name=name, version=version,
                    payload_dir=payload_dir, out_path=out_path,
                    on_line=self.line_received.emit,
                )
                self._build_done.emit(True, str(out_path))
            except PackageBuildError as e:
                self._build_done.emit(False, str(e))
            except Exception as e:
                # Safety net -- see _on_deploy's worker for the same
                # fix and why: without this, any other exception here
                # left build_btn stuck on "Building…" forever.
                self.line_received.emit(f"[package] unexpected {type(e).__name__}: {e}")
                self._build_done.emit(False, f"unexpected {type(e).__name__}: {e}")

        threading.Thread(target=worker, name="build-package", daemon=True).start()

    @Slot(bool, str)
    def _on_build_done(self, ok: bool, msg: str) -> None:
        self.build_btn.setEnabled(True)
        self.build_btn.setText("Build package")
        if ok:
            self._append_line(f"[package] built: {msg}")
            self._last_built_package = Path(msg)
            self.deploy_pkg_path.setText(msg)
        else:
            self._append_line(f"[package] FAILED: {msg}")

    # ---- deploy ---------------------------------------------------------

    def _on_browse_deploy_pkg(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, "Choose .moonpkg", str(Path(self.out_path.text()).parent),
            "MooN package (*.moonpkg)",
        )
        if p:
            self.deploy_pkg_path.setText(p)

    def _on_deploy(self) -> None:
        s = self._get_settings()
        pkg_path = Path(self.deploy_pkg_path.text().strip())
        if not pkg_path.is_file():
            QMessageBox.warning(self, "No package", f"Not a file: {pkg_path}")
            return
        node_id = self.target_node.currentData()
        if node_id is None:
            QMessageBox.warning(self, "No node", "Configure and pick a target node first.")
            return
        nodes = {n.node_id: n for n in nodes_from_json(s.hardware_nodes_json)}
        node = nodes.get(node_id)
        if node is None:
            QMessageBox.warning(self, "No node", "Selected node no longer exists.")
            return

        set_ip = self.set_ip_chk.isChecked()
        new_ip = self.new_ip.text().strip()
        gateway = self.gateway.text().strip()
        dns = self.dns.text().strip()
        net_iface = self.net_iface.text().strip() or "eth0"
        do_reboot = self.reboot_chk.isChecked()
        if set_ip and not new_ip:
            QMessageBox.warning(self, "No address", "Enter a new address (CIDR) or untick 'Also set static IP'.")
            return
        if set_ip and "/" not in new_ip:
            # A bare IP with no prefix length means /32 to systemd-networkd
            # (Address=) -- that's a host-only route with no subnet, so
            # the gateway route can't come up on-link and the node
            # effectively loses network reachability. Reject rather than
            # silently deploying a config that bricks connectivity.
            QMessageBox.warning(
                self, "Missing CIDR prefix",
                f"'{new_ip}' has no /prefix length (e.g. '{new_ip}/24'). "
                f"Without it systemd-networkd treats it as /32 -- a "
                f"host-only route with no subnet, so the gateway can't "
                f"come up on-link and the node loses network reachability.",
            )
            return

        self.deploy_btn.setEnabled(False)
        self.deploy_btn.setText("Deploying…")
        self._append_line(f"\n=== deploy {pkg_path.name} → {node.host} ===")

        def worker() -> None:
            ok = True
            try:
                self.line_received.emit(f"[deploy] {node.host} → /tmp/moon-package.moonpkg.new")
                scp_file(node, pkg_path, "/tmp/moon-package.moonpkg.new", timeout=60.0)
                self.line_received.emit(f"[deploy] {node.host} copying into place ({_REMOTE_PKG_PATH})")
                ssh_exec(
                    node,
                    # cp, not mv: /boot/firmware is vfat, which can't
                    # represent Unix ownership -- mv tripped trying to
                    # preserve/set it across that boundary (found +
                    # fixed the same way in tools/moon-deploy.sh).
                    f"sudo cp /tmp/moon-package.moonpkg.new {_REMOTE_PKG_PATH} && "
                    f"sudo chmod 644 {_REMOTE_PKG_PATH} && "
                    f"rm -f /tmp/moon-package.moonpkg.new",
                    timeout=60.0,  # SD card I/O can be slow, give it real room
                )
                self.line_received.emit(f"[deploy] {node.host} reloading (moon-pkg-load + moon-node)")
                ssh_exec(
                    node,
                    "sudo systemctl restart moon-pkg-load.service && "
                    "sudo systemctl restart moon-node.service",
                    timeout=60.0,  # moon-pkg-load extracts + sha256-checks the payload off the SD card
                )
                time.sleep(1.0)

                # moon-pkg-load.service is the on-target verifier (see
                # core/moon_package.py): it checks manifest.sig + payload_sha256
                # and extracts payload.tar.gz to /opt/moon. That's the actual
                # "did THIS deploy succeed" signal -- independent of any other
                # node. It's Type=oneshot without RemainAfterExit, so
                # `is-active` goes back to "inactive" the moment it finishes
                # either way; `Result` is what persists and reflects the exit
                # code of the last run ("success" vs "exit-code"/"failed").
                pkg_result = ssh_exec(
                    node,
                    "systemctl show -p Result --value moon-pkg-load.service",
                    timeout=30.0,
                ).strip()
                if pkg_result == "success":
                    self.line_received.emit(
                        f"[deploy] {node.host} moon-pkg-load.service verified + extracted the new package"
                    )
                else:
                    ok = False
                    self.line_received.emit(
                        f"[deploy] {node.host} moon-pkg-load.service did NOT succeed "
                        f"(Result={pkg_result or 'unknown'}) -- "
                        f"check 'journalctl -u moon-pkg-load' on the node"
                    )

                # moon-node.service is reported for visibility only -- it can
                # legitimately stay non-active until enough peer nodes are up
                # for quorum (nominal/minimum), so its state must not gate
                # deploy success/failure.
                node_status = ssh_exec(
                    node,
                    "systemctl is-active --quiet moon-node.service && echo ACTIVE || echo INACTIVE",
                    timeout=30.0,
                )
                if "ACTIVE" in node_status:
                    self.line_received.emit(f"[deploy] {node.host} moon-node.service is active")
                else:
                    self.line_received.emit(
                        f"[deploy] {node.host} moon-node.service not yet active "
                        f"(expected until enough peer nodes are up for quorum)"
                    )

                if set_ip:
                    ro_remounted = self._deploy_set_ip(node, new_ip, gateway, dns, net_iface)
                    if not ro_remounted and not do_reboot:
                        self.line_received.emit(
                            f"[deploy] {node.host} WARNING: rootfs is left read-write -- "
                            f"reboot it (or re-run this with 'Reboot after deploy') to "
                            f"get back to read-only"
                        )

                if do_reboot:
                    self.line_received.emit(f"[deploy] {node.host} rebooting")
                    try:
                        ssh_exec(node, "sudo reboot", timeout=15.0)
                    except SshError:
                        pass  # connection drops as the node goes down -- expected

                    # The pkg_result check above ran against the pre-reboot
                    # live-restart -- it says nothing about whether
                    # moon-pkg-load.service (which also runs at boot, it's
                    # enabled) actually succeeded on THIS reboot. Wait for
                    # the node to come back and check that boot's result
                    # for real, rather than reporting success/failure based
                    # on a state that's about to be replaced by a fresh boot.
                    reboot_host = new_ip.split("/")[0] if set_ip else node.host
                    reboot_node = node if not set_ip else replace(node, host=reboot_host)
                    self.line_received.emit(f"[deploy] waiting for {reboot_host} to come back up…")
                    if wait_for_reachable(reboot_node):
                        time.sleep(1.0)  # give moon-pkg-load.service a moment to finish its boot-time run
                        boot_pkg_result = ssh_exec(
                            reboot_node,
                            "systemctl show -p Result --value moon-pkg-load.service",
                            timeout=30.0,
                        ).strip()
                        if boot_pkg_result == "success":
                            ok = True
                            self.line_received.emit(
                                f"[deploy] {reboot_host} back up -- moon-pkg-load.service "
                                f"succeeded on this boot"
                            )
                        else:
                            ok = False
                            self.line_received.emit(
                                f"[deploy] {reboot_host} back up but moon-pkg-load.service did "
                                f"NOT succeed on this boot (Result={boot_pkg_result or 'unknown'}) "
                                f"-- check 'journalctl -u moon-pkg-load' on the node"
                            )
                    else:
                        ok = False
                        self.line_received.emit(
                            f"[deploy] {reboot_host} did not come back up within 90s -- check manually"
                        )
                elif set_ip:
                    self.line_received.emit(
                        "[deploy] new IP written but NOT applied live -- reboot the node "
                        "yourself (or re-run with 'Reboot after deploy') to actually switch to it"
                    )

                self._deploy_done.emit(ok)
            except SshError as e:
                self.line_received.emit(f"[deploy] {node.host} FAILED: {e.output}")
                self._deploy_done.emit(False)
            except Exception as e:
                # Safety net: ANY other exception here used to kill this
                # background thread silently (Python threads just print
                # a traceback to stderr and die) -- _deploy_done never
                # fired, so deploy_btn stayed stuck on "Deploying…"
                # forever with no visible error anywhere in the UI. This
                # guarantees the button always gets re-enabled and the
                # failure is at least visible in the log, no matter what
                # actually went wrong.
                self.line_received.emit(
                    f"[deploy] {node.host} FAILED (unexpected {type(e).__name__}): {e}"
                )
                self._deploy_done.emit(False)

        threading.Thread(target=worker, name="deploy-package", daemon=True).start()

    def _deploy_set_ip(self, node: HardwareNode, cidr: str, gateway: str, dns: str, iface: str) -> bool:
        """Write a new static-IP systemd-networkd config, same content
        and target path moon-deploy.sh's --ip uses. Never applied live
        -- see reboot handling in _on_deploy (matches that script's
        deliberate "don't restart networking over the session you're
        connected through" safety behaviour).

        Returns whether the ro-remount at the end succeeded (best-effort,
        see below) -- NOT whether the config write itself succeeded,
        that part raises SshError like everything else if it fails."""
        self.line_received.emit(f"[deploy] {node.host} preparing static IP config for {iface}: {cidr}")
        lines = ["[Match]", f"Name={iface}", "", "[Network]", f"Address={cidr}"]
        if gateway:
            lines.append(f"Gateway={gateway}")
        if dns:
            lines.append(f"DNS={dns}")
        lines += ["IPv6AcceptRA=no", "LinkLocalAddressing=no"]
        content = "\n".join(lines) + "\n"

        scp_bytes(node, content.encode(), "/tmp/10-eth0-static.network.new", timeout=60.0)
        self.line_received.emit(f"[deploy] {node.host} remounting rootfs rw, installing network config")
        # rw-remount + install must succeed -- that's the actual config
        # write. Kept as one command so a failure partway through (e.g.
        # rw-remount ok but install fails) doesn't leave the rootfs
        # writable with nothing to show for it.
        ssh_exec(
            node,
            "sudo /usr/local/sbin/moon-remount-rw.sh && "
            "sudo install -m 644 /tmp/10-eth0-static.network.new "
            "/etc/systemd/network/10-eth0-static.network && "
            "rm -f /tmp/10-eth0-static.network.new",
            timeout=30.0,
        )
        # ro-remount is best-effort, deliberately separate: it can fail
        # with "mount point is busy" (EBUSY) if something transiently
        # still has / open for writing (seen in the field --
        # systemd-timesyncd persisting its clock is the likely culprit).
        # The config write above already succeeded regardless, and if a
        # reboot follows, fstab remounts / read-only fresh on its own --
        # so this failing shouldn't block the rest of the deploy.
        self.line_received.emit(f"[deploy] {node.host} remounting ro")
        try:
            ssh_exec(node, "sudo /usr/local/sbin/moon-remount-ro.sh", timeout=30.0)
            return True
        except SshError as e:
            self.line_received.emit(
                f"[deploy] {node.host} remount-ro failed (continuing -- "
                f"a reboot remounts read-only fresh via fstab anyway): {e.output}"
            )
            return False

    @Slot(bool)
    def _on_deploy_done(self, ok: bool) -> None:
        self.deploy_btn.setEnabled(True)
        self.deploy_btn.setText(f"Push to {_REMOTE_PKG_PATH} + restart")

    # ---- log --------------------------------------------------------------

    @Slot(str)
    def _append_line(self, line: str) -> None:
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(line + "\n")
        self.log.moveCursor(QTextCursor.End)
