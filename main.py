import sys
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIntValidator
from PySide6.QtCore import QThread, Signal
from main_window import Ui_MainWindow
from util.udp_com import UdpCom

sock : UdpCom = None

class UdpListener(QThread):
    message_received = Signal(str)  # signal to send message to UI

    def __init__(self, udp_com):
        super().__init__()
        self.udp_com = udp_com
        self._running = True

    def run(self):
        while self._running and self.udp_com and self.udp_com.udp_socket:
            try:
                data, addr = self.udp_com.udp_socket.recvfrom(4096)
                msg = data.decode("utf-8")
                self.message_received.emit(msg)
            except BlockingIOError:
                self.msleep(50)  # sleep 50ms and try again
            except Exception as e:
                break

    def stop(self):
        self._running = False
        self.quit()
        self.wait()


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
        self.listener_thread = None
    
    def closeEvent(self, event):
        global sock
        if hasattr(self, "listener_thread") and self.listener_thread:
            self.listener_thread.stop()
            self.listener_thread = None
        if sock:
            sock.drop_socket()
            sock = None
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

    def port_lineedit_handler(self):
        self.ui.connectSocketButton.setEnabled(self.ui.portLineEdit.hasAcceptableInput())

    def socket_button_handler(self):
        global sock
        if sock is not None:
            sock.drop_socket()
            self.log_to_debug_textbox("Dropped socket.")
            if self.listener_thread:
                self.listener_thread.stop()
                self.log_to_debug_textbox("Dropped ListenerThread.")
        ip_addr = "0.0.0.0"
        port = int(self.ui.portLineEdit.text())
        try:
            sock = UdpCom(ip_addr, port)
            self.log_to_debug_textbox("Successfully connected to socket at " + ip_addr + ":" + str(port))
            self.listener_thread = UdpListener(sock)
            self.listener_thread.message_received.connect(self.log_to_debug_textbox)
            self.listener_thread.start()
        except:
            self.log_to_debug_textbox("Error connecting to socket at " + ip_addr + ":" + str(port))



if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = UI()
    win.show()
    app.exec()
