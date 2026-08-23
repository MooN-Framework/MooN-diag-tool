"""
Qt bridge: the core objects (DiagClient, OperationalListener, Registry)
have no Qt dependency. This class translates their callbacks into Qt
signals so the UI can react on them thread-safely.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    op_frame = Signal(object, float)   # DecodedFrame, ts_mono
    op_frame_batch = Signal(list)      # list[tuple[DecodedFrame, float]] -- see main_window's
                                        # _flush_op_frame_queue: batches high-frequency UDP arrivals
                                        # into one queued cross-thread Qt event instead of one per
                                        # frame, which was flooding the GUI event loop at small
                                        # cycle_ms and making the whole window feel like it hung.
    diag_telegram = Signal(object)     # DiagTelegram
    node_changed = Signal(object)      # NodeView
    log_message = Signal(str)          # free-form text for the log tab
