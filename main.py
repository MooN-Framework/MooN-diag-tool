import sys
import logging
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIntValidator, QIcon, QFont
from PySide6.QtCore import QSettings, Qt
from main_window import Ui_MainWindow
from util.udp_com import UdpCom, UdpListener
from util.net_helper import *
from util.log_parse_manager import *
from util.node_state_manager import *
from datetime import datetime

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
        self.ui.portLineEdit.textChanged.connect(self.on_socket_lineedits_changed)
        port_validator = QIntValidator(0, 65535, self)
        self.ui.portLineEdit.setValidator(port_validator)
        self.ui.portLineEdit.setMaxLength(5)
        # Init ipTextbox
        self.ui.ipLineEdit.clear()
        self.ui.ipLineEdit.textChanged.connect(self.on_socket_lineedits_changed)
        self.ui.ipLineEdit.setMaxLength(15)
        # Init connection button
        self.ui.connectSocketButton.setEnabled(False)
        self.ui.connectSocketButton.clicked.connect(self.on_socket_button_clicked)
        self.ui.connectSocketButton.setStyleSheet("""
        QPushButton:disabled {
            color: gray;
            background-color: lightgray;
        }
        """)
        # Filter combobox
        self.ui.comboBox.currentTextChanged.connect(self.on_filter_combobox_changed)
        # Init cmd button
        self.ui.send_cmd_button.setEnabled(False)
        self.ui.send_cmd_button.clicked.connect(self.on_send_cmd_button_clicked)
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
        self.ui.cmd_lineEdit.textChanged.connect(self.on_cmd_lineedit_changed)
        # Init connect on startup checkbox
        self.ui.checkBox.stateChanged.connect(self.on_startup_connect_handler)
        # Init own classes
        self.listener_thread = None
        self.sock = UdpCom()
        self.sock.socket_state_changed.connect(self.on_socket_state_changed)
        self.parse_log_manager = LogParseManager()
        self.parse_log_manager.detected_new_node.connect(self.on_new_node_deteced)
        self.node_state_manager = NodeStateManager()
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
            self.on_socket_button_clicked()

    # ====================
    #   System Cleanup
    # ====================
    def closeEvent(self, event):
        if hasattr(self, "listener_thread") and self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None
        if self.sock:
            self.sock.drop_socket()
            self.sock = None
        event.accept()

    # ====================
    #   Widget handler
    # ====================
    def on_socket_state_changed(self, state : bool):
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(state and id_valid)
    
    def on_startup_connect_handler(self, state):
        self.settings.setValue("connect_startup", state)
            
    def on_socket_button_clicked(self):
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
            self.listener_thread.message_received.connect(self.on_msg_received_state_update)
            self.listener_thread.start()
            self.log_to_debug_textbox("Successfully connected to socket at " + ip_addr + ":" + str(port))
            self.settings.setValue("port",self.ui.portLineEdit.text())
            self.settings.setValue("broadcastip", self.ui.ipLineEdit.text())
        except:
            self.log_to_debug_textbox("Error connecting to socket at " + ip_addr + ":" + str(port))

    def on_socket_lineedits_changed(self):
        valid = self.is_ip_and_port_valid()
        self.ui.connectSocketButton.setEnabled(valid)

    def on_cmd_lineedit_changed(self):
        socket_valid = self.sock.udp_socket is not None
        id_valid = self.ui.cmd_lineEdit.hasAcceptableInput()
        self.ui.send_cmd_button.setEnabled(socket_valid and id_valid)

    def on_send_cmd_button_clicked(self):
        cmd_str = self.ui.cmd_comboBox.currentText()
        cmd_id = self.str_to_cmd_int(cmd_str)
        cmd_sys_id = int(self.ui.cmd_lineEdit.text())
        self.sock.send_msg(f"MASTER:{cmd_sys_id}:InitialSync:{cmd_id}")
        self.settings.setValue("cmd_id", self.ui.cmd_lineEdit.text())

    def on_new_node_deteced(self, id : int):
        self.ui.comboBox.addItem("NODE "+ str(id))
        self.node_state_manager.add_status_row(self.ui.status_grid, id)

    def on_filter_combobox_changed(self):
        curr_filter_id_str = self.ui.comboBox.currentText()
        if curr_filter_id_str == "ALL":
            self.parse_log_manager.set_node_filter("ALL")
            return
        id = int(curr_filter_id_str.split(" ")[1])
        self.parse_log_manager.set_node_filter(id)
    
    def on_msg_received_state_update(self, msg : str):
        splitted_log = msg.split(":")
        if splitted_log[0] == "LOG":
            system_id = int(splitted_log[1])
            system_state = splitted_log[2]
            self.node_state_manager.set_id_state(self.ui.status_grid, system_id, system_state)
        
    # ====================
    #   Helper Methods
    # ====================
    def log_to_debug_textbox(self, log: str):
        fmt_log = self.parse_log_manager.parse_generic_log(log)
        if fmt_log is None or fmt_log == "":
            return
        self.ui.loggingTextBox.append(fmt_log)
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
    
    def str_to_cmd_int(self, string : str) -> int:
        match string:
            case "Print Recv Msg":
                cmd_id = 0
            case "Induce CRC fault":
                cmd_id = 1
            case "Induce Voting fault":
                cmd_id = 2
        return cmd_id

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = UI()
    win.show()
    app.exec()
