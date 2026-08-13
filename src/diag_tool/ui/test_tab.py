"""
Test tab: runs pytest against the scenarios directory.

Modes:
- simulated: local pytest as usual; conftest spawns nodes via cargo.
- hardware:  pytest is called with --fabric=hardware plus the GUI's
             multicast config. No nodes are spawned locally.

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
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

    def __init__(self, settings_provider, parent=None) -> None:
        super().__init__(parent)
        self._get_settings = settings_provider
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None

        self._build()
        self.line_received.connect(self._append_line)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        # Header
        head = QHBoxLayout()
        title = QLabel("Test runner"); title.setProperty("heading", True)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(QLabel("Mode:"))
        self.mode = QComboBox()
        self.mode.addItems(["simulated", "hardware"])
        head.addWidget(self.mode)
        self.refresh_btn = QPushButton("Load scenarios")
        self.refresh_btn.clicked.connect(self._reload_scenarios)
        head.addWidget(self.refresh_btn)
        outer.addLayout(head)

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

        self.help_btn = QPushButton("Hardware mode: show conftest patch")
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

    def _scenarios_dir(self) -> Optional[Path]:
        s = self._get_settings()
        if not s.scenarios_path:
            return None
        p = Path(s.scenarios_path)
        return p if p.is_dir() else None

    def _reload_scenarios(self) -> None:
        d = self._scenarios_dir()
        self.scenarios_list.clear()
        if d is None:
            QMessageBox.warning(self, "No directory",
                                "Set 'Scenarios directory' in Settings first.")
            return
        for p in sorted(d.glob("test_*.py")):
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, str(p))
            self.scenarios_list.addItem(it)

    def _on_stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _on_run(self) -> None:
        s = self._get_settings()
        d = self._scenarios_dir()
        if d is None:
            QMessageBox.warning(self, "No directory",
                                "Set 'Scenarios directory' in Settings first.")
            return

        selected = [self.scenarios_list.item(i).data(Qt.UserRole)
                    for i in range(self.scenarios_list.count())
                    if self.scenarios_list.item(i).isSelected()]

        cmd = [sys.executable, "-m", "pytest"]
        cmd += shlex.split(self.extra_args.text() or "")

        if self.mode.currentText() == "hardware":
            cmd += [
                "--fabric=hardware",
                f"--diag-group={s.diag_group}",
                f"--diag-port={s.diag_port}",
                f"--op-group={s.op_group}",
                f"--op-port={s.op_port}",
                f"--interface-ip={s.interface_ip}",
            ]

        cmd += selected if selected else [str(d)]
        repo_root = d.parent

        env = os.environ.copy()
        for line in self.env_edit.toPlainText().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

        self.output.clear()
        self._append_line(f"$ (cwd={repo_root}) " + " ".join(shlex.quote(c) for c in cmd))
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=repo_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as e:
            self._append_line(f"[Error] pytest could not be started: {e}")
            return

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._reader = threading.Thread(target=self._read_loop, name="pytest-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self.line_received.emit(line.rstrip("\n"))
        rc = self._proc.wait()
        self.line_received.emit(f"[pytest exit={rc}]")
        self.line_received.emit("__RUN_DONE__")

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
        pkg_dir = Path(__file__).resolve().parent.parent.parent.parent
        doc = pkg_dir / "docs" / "harness_hardware.md"
        text = doc.read_text() if doc.exists() else "docs/harness_hardware.md not found."
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Hardware mode: conftest patch")
        dlg.setTextFormat(Qt.MarkdownText)
        dlg.setText(text)
        dlg.exec()
