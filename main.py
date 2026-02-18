import sys
import logging
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIntValidator, QIcon
from main_window import Ui_MainWindow
from util.udp_com import UdpCom, UdpListener
from util.net_helper import *


logger = logging.getLogger("DiagnoseSwbft")
logger.setLevel(logging.DEBUG)

class UI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # Init logging textbox
        self.ui.loggingTextBox.clear()
        # Init portTextbox
        self.ui.portLineEdit.clear()
        self.ui.portLineEdit.textChanged.connect(self.update_socket_button_handler)
        port_validator = QIntValidator(0, 65535, self)
        self.ui.portLineEdit.setValidator(port_validator)
        self.ui.portLineEdit.setMaxLength(5)
        # Init ipTextbox
        self.ui.ipLineEdit.clear()
        self.ui.ipLineEdit.textChanged.connect(self.update_socket_button_handler)
        self.ui.ipLineEdit.setMaxLength(15)
        # Init connection button
        self.ui.connectSocketButton.setEnabled(False)
        self.ui.connectSocketButton.clicked.connect(self.socket_button_handler)
        self.ui.connectSocketButton.setStyleSheet("""
        QPushButton:disabled {
            color: gray;
            background-color: lightgray;
        }
        """)
        # Init cmd button
        self.ui.send_cmd_button.setEnabled(False)
        self.ui.send_cmd_button.clicked.connect(self.send_cmd_button_handler)
        self.ui.send_cmd_button.setStyleSheet("""
        QPushButton:disabled {
            color: gray;
            background-color: lightgray;
        }
        """)
        # Init cmd_id_lineedit
        id_validator = QIntValidator(0, 255, self)
        self.ui.cmd_lineEdit.clear()
        self.ui.cmd_lineEdit.setValidator(id_validator)
        self.ui.cmd_lineEdit.setMaxLength(3)
        self.ui.cmd_lineEdit.textChanged.connect(self.update_cmd_button_handler)
        # Init combobox
        self.ui.debug_level_comboBox.currentIndexChanged.connect(self.debug_level_handler)
        # Init own classes
        self.listener_thread = None
        self.sock = UdpCom()
        self.sock.socket_state_changed.connect(self.on_socket_state_changed)
        
    def closeEvent(self, event):
        if hasattr(self, "listener_thread") and self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None
        if self.sock:
            self.sock.drop_socket()
            self.sock = None
        event.accept()
    
    def log_to_debug_textbox(self, log: str):
        self.ui.loggingTextBox.append(log)
        doc = self.ui.loggingTextBox.document()
        while doc.blockCount() > 6000:
            cursor = QtGui.QTextCursor(doc)
            cursor.movePosition(QtGui.QTextCursor.Start)
            cursor.select(QtGui.QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def debug_level_handler(self):
        print(self.ui.debug_level_comboBox.currentText())

    def update_socket_button_handler(self):
        ip_valid = is_valid_ipv4(self.ui.ipLineEdit.text())
        port_valid = self.ui.portLineEdit.hasAcceptableInput()
        self.ui.connectSocketButton.setEnabled(ip_valid and port_valid)

    def update_cmd_button_handler(self):
        socket_valid = self.sock.udp_socket is not None
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(socket_valid and id_valid)

    def on_socket_state_changed(self, state : bool):
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(state and id_valid)

    def send_cmd_button_handler(self):
        pass

    def socket_button_handler(self):
        if self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None
            self.log_to_debug_textbox("Dropped ListenerThread.")
        if self.sock.udp_socket is not None:
            self.sock.drop_socket()
            self.log_to_debug_textbox("Dropped socket.")

        ip_addr = "0.0.0.0"
        port = int(self.ui.portLineEdit.text())
        broadcast_addr = self.ui.ipLineEdit.text()
        try:
            self.sock.connect_socket(ip_addr, port, broadcast_addr)
            self.log_to_debug_textbox("Successfully connected to socket at " + ip_addr + ":" + str(port))
            self.listener_thread = UdpListener(self.sock)
            self.listener_thread.message_received.connect(self.log_to_debug_textbox)
            self.listener_thread.start()
        except:
            self.log_to_debug_textbox("Error connecting to socket at " + ip_addr + ":" + str(port))

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = UI()
    win.show()
    app.exec()
