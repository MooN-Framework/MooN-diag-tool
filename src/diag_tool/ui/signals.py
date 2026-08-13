"""
Qt bridge: the core objects (DiagClient, OperationalListener, Registry)
have no Qt dependency. This class translates their callbacks into Qt
signals so the UI can react on them thread-safely.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    op_frame = Signal(object, float)   # DecodedFrame, ts_mono
    diag_telegram = Signal(object)     # DiagTelegram
    node_changed = Signal(object)      # NodeView
    log_message = Signal(str)          # free-form text for the log tab
