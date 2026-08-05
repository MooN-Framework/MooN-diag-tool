"""Session-Manager — verdrahtet Worker, Threads und Session-Log.

Wird von MainWindow gehalten. Verantwortlich für:
- Start/Stop beider Worker in eigenen QThreads
- Weiterleitung empfangener Frames an Log + Signals
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot

from diag_tool.config import AppConfig
from diag_tool.core.session_log import SessionLog
from diag_tool.net.command import CommandWorker
from diag_tool.net.frame import UdpFrame
from diag_tool.net.listener import ListenerWorker


class Session(QObject):
    """Aktive Diag-Session. Emittiert Ereignisse an die UI."""

    system_frame = Signal(object)  # UdpFrame vom Betriebsmulticast
    diagnose_frame = Signal(object)  # UdpFrame vom Diagnose-Multicast
    error = Signal(str)
    log_path_changed = Signal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

        self._listener = ListenerWorker(config.network.system)
        self._listener_thread = QThread()
        self._listener.moveToThread(self._listener_thread)
        self._listener_thread.started.connect(self._listener.start)
        self._listener.frame_received.connect(self._on_system_frame)
        self._listener.error.connect(self.error)

        self._command = CommandWorker(config.network.diagnose)
        self._command_thread = QThread()
        self._command.moveToThread(self._command_thread)
        self._command_thread.started.connect(self._command.start)
        self._command.frame_received.connect(self._on_diagnose_frame)
        self._command.frame_sent.connect(self._on_diagnose_sent)
        self._command.error.connect(self.error)

        self._session_log = SessionLog(config.logging.session_dir)
        self._log_open = False

    def start(self) -> None:
        self._session_log.__enter__()
        self._log_open = True
        if self._session_log.path is not None:
            self.log_path_changed.emit(str(self._session_log.path))
        self._listener_thread.start()
        self._command_thread.start()

    def stop(self) -> None:
        self._listener.stop()
        self._command.stop()
        self._listener_thread.quit()
        self._command_thread.quit()
        self._listener_thread.wait(2000)
        self._command_thread.wait(2000)
        if self._log_open:
            self._session_log.__exit__(None, None, None)
            self._log_open = False

    @Slot(object)
    def send_diagnose_frame(self, frame: UdpFrame) -> None:
        """Vom UI-Thread aufrufbar — an CommandWorker weiterreichen."""
        # Direktaufruf ist ok: send_frame ist als Slot deklariert und
        # QueuedConnection wird vom Meta-Object-System sichergestellt.
        self._command.send_frame(frame)

    @Slot(object)
    def _on_system_frame(self, frame: UdpFrame) -> None:
        self._session_log.log(
            "system_frame",
            kind=frame.kind.name,
            node_id=frame.node_id,
            seq=frame.seq,
            payload_len=len(frame.payload),
        )
        self.system_frame.emit(frame)

    @Slot(object)
    def _on_diagnose_frame(self, frame: UdpFrame) -> None:
        self._session_log.log(
            "diagnose_rx",
            kind=frame.kind.name,
            node_id=frame.node_id,
            seq=frame.seq,
        )
        self.diagnose_frame.emit(frame)

    @Slot(object)
    def _on_diagnose_sent(self, frame: UdpFrame) -> None:
        self._session_log.log(
            "diagnose_tx",
            kind=frame.kind.name,
            node_id=frame.node_id,
            seq=frame.seq,
        )
