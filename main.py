import sys
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtUiTools import QUiLoader

loader = QUiLoader()
app = QtWidgets.QApplication(sys.argv)
window = loader.load("main_window.ui", None)
window.show()
app.exec()
