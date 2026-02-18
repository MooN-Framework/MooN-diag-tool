import sys
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIntValidator
from main_window import Ui_MainWindow
from util.udp_com import UdpCom

sock : UdpCom = None

class UI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # Init logging textbox
        self.ui.loggingTextBox.clear()
        # Init portTextbox
        self.ui.portLineEdit.clear()
        self.ui.portLineEdit.textChanged.connect(self.port_lineedit_handler)
        validator = QIntValidator(0, 65535, self)
        self.ui.portLineEdit.setValidator(validator)
        self.ui.portLineEdit.setMaxLength(5)
        # Init connection button
        self.ui.connectSocketButton.setEnabled(False)
        self.ui.connectSocketButton.clicked.connect(self.socket_button_handler)
        self.ui.connectSocketButton.setStyleSheet("""
        QPushButton:disabled {
            color: gray;
            background-color: lightgray;
        }
        """)
        # Init combobox
        self.ui.debug_level_comboBox.currentIndexChanged.connect(self.debug_level_handler)
        
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

    def port_lineedit_handler(self):
        self.ui.connectSocketButton.setEnabled(self.ui.portLineEdit.hasAcceptableInput())

    def socket_button_handler(self):
        global sock
        if sock is not None:
            sock.drop_socket()
            self.log_to_debug_textbox("Dropped socket.")
        ip_addr = "0.0.0.0"
        port = int(self.ui.portLineEdit.text())
        try:
            sock = UdpCom(ip_addr, port)
            self.log_to_debug_textbox("Successfully connected to socket at " + ip_addr + ":" + str(port))
            sock.send_msg("Test payload")
        except:
            self.log_to_debug_textbox("Error connecting to socket at " + ip_addr + ":" + str(port))



if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = UI()
    win.show()
    app.exec()
