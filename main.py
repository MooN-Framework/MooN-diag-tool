import sys
import logging
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIntValidator, QIcon, QFont
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QGridLayout, QLabel
)
from main_window import Ui_MainWindow
from util.udp_com import UdpCom, UdpListener
from util.net_helper import *
from datetime import datetime

logger = logging.getLogger("DiagnoseSwbft")
logger.setLevel(logging.DEBUG)

COLORS = {
    "INFO": "#2196F3",
    "SUCCESS": "#4CAF50",
    "WARNING": "#FFC107",
    "ERROR": "#F44336"
}

class LedIndicator(QLabel):
    def __init__(self, color="red", size=16):
        super().__init__()
        self.setFixedSize(size, size)
        self.setStyleSheet(f"""
            background-color: {color};
            border-radius: {size // 2}px;
            border: 1px solid black;
        """)

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
        # Init connect on startup checkbox
        self.ui.checkBox.stateChanged.connect(self.on_startup_connect_handler)
        # Init own classes
        self.listener_thread = None
        self.sock = UdpCom()
        self.sock.socket_state_changed.connect(self.on_socket_state_changed)
        # Load User Settings
        self.settings = QSettings("KW", "SwbftDiagnoseTool")
        loaded_port = self.settings.value("port","")
        loaded_broadcastip = self.settings.value("broadcastip", "")
        loaded_cmd_id = self.settings.value("cmd_id", "")
        loaded_on_startup_connect = self.settings.value("connect_startup", False, type=bool)
        self.ui.portLineEdit.setText(loaded_port)
        self.ui.ipLineEdit.setText(loaded_broadcastip)
        self.ui.cmd_lineEdit.setText(loaded_cmd_id)
        self.ui.checkBox.setChecked(loaded_on_startup_connect)
        # After load settings stuff
        if loaded_on_startup_connect and self.is_ip_and_port_valid():
            self.socket_button_handler()
    
    def add_status_row(self, id, state, row):
        label = QLabel("SystemID: Node" + str(id))
        label2 = QLabel("Systemstatus: " + state)
        led = LedIndicator("green")
        font = QFont()
        font.setPointSize(11)  # 11 pt
        label.setFont(font)
        label2.setFont(font)
        self.ui.status_grid.addWidget(label, row, 0, alignment=Qt.AlignTop)
        self.ui.status_grid.addWidget(label2, row, 0 + 1, alignment=Qt.AlignTop)
        self.ui.status_grid.addWidget(led, row, 0 + 2, alignment=Qt.AlignTop)

    def closeEvent(self, event):
        if hasattr(self, "listener_thread") and self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None
        if self.sock:
            self.sock.drop_socket()
            self.sock = None
        event.accept()
    
    def parse_master_msg(self, splitted_log: str) -> str:
        return "[MASTER] ==> NODE" + splitted_log[1]  + " ==> execute " + self.int_to_cmd_str(int(splitted_log[3]))

    def parse_system_msg(self, splitted_log) -> str:
        if len(splitted_log) == 3:
            return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "]"
        elif len(splitted_log) == 4:
            return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "] Value(" + hex(int(splitted_log[3])) + ")" 
        
    def parse_system_log(self, splitted_log: str) -> str:
        return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "] " + splitted_log[3] 

    def log_to_debug_textbox(self, log: str):
        now = datetime.now().strftime("%H:%M:%S")
        splitted_log = log.split(":")
        print(splitted_log)
        fmt_log = log
        color = COLORS["INFO"]
        match splitted_log[0]:
            case "MASTER":
                fmt_log = self.parse_master_msg(splitted_log)
                color = COLORS["ERROR"]
            case "SYSTEM":
                fmt_log = self.parse_system_msg(splitted_log)
                return
            case "LOG":
                fmt_log = self.parse_system_log(splitted_log)
        
        self.ui.loggingTextBox.append(f'<span style="color:gray;">[{now}]</span> '
        f'<span style="color:{color};"></span> '
        f'{fmt_log}')
        doc = self.ui.loggingTextBox.document()
        while doc.blockCount() > 6000:
            cursor = QtGui.QTextCursor(doc)
            cursor.movePosition(QtGui.QTextCursor.Start)
            cursor.select(QtGui.QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def is_ip_and_port_valid(self) -> bool:
        ip_valid = is_valid_ipv4(self.ui.ipLineEdit.text())
        port_valid = self.ui.portLineEdit.hasAcceptableInput()
        return ip_valid and port_valid

    def update_socket_button_handler(self):
        valid = self.is_ip_and_port_valid()
        self.ui.connectSocketButton.setEnabled(valid)

    def update_cmd_button_handler(self):
        socket_valid = self.sock.udp_socket is not None
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(socket_valid and id_valid)

    def on_socket_state_changed(self, state : bool):
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(state and id_valid)

    def int_to_cmd_str(self, cmd_int : int) -> str:
        match cmd_int:
            case 0:
                cmd_str = "Print Recv Msg"
            case 1:
                cmd_str = "Induce CRC fault"
            case 2:
                cmd_str = "Induce Voting fault"
        return cmd_str
    
    def str_to_cmd_int(self, string : str) -> int:
        match string:
            case "Print Recv Msg":
                cmd_id = 0
            case "Induce CRC fault":
                cmd_id = 1
            case "Induce Voting fault":
                cmd_id = 2
        return cmd_id

    def send_cmd_button_handler(self):
        cmd_str = self.ui.cmd_comboBox.currentText()
        cmd_id = self.str_to_cmd_int(cmd_str)
        cmd_sys_id = int(self.ui.cmd_lineEdit.text())
        self.sock.send_msg(f"MASTER:{cmd_sys_id}:InitialSync:{cmd_id}")
        self.settings.setValue("cmd_id", self.ui.cmd_lineEdit.text())

    def on_startup_connect_handler(self, state):
        self.settings.setValue("connect_startup", state)

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
            self.listener_thread = UdpListener(self.sock)
            self.listener_thread.message_received.connect(self.log_to_debug_textbox)
            self.listener_thread.start()
            self.log_to_debug_textbox("Successfully connected to socket at " + ip_addr + ":" + str(port))
            self.settings.setValue("port",self.ui.portLineEdit.text())
            self.settings.setValue("broadcastip", self.ui.ipLineEdit.text())
        except:
            self.log_to_debug_textbox("Error connecting to socket at " + ip_addr + ":" + str(port))

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = UI()
    win.show()
    app.exec()
