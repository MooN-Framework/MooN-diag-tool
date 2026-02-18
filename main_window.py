# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_windowUGtebq.ui'
##
## Created by: Qt User Interface Compiler version 6.10.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QTabWidget,
    QTextEdit, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(640, 420)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(MainWindow.sizePolicy().hasHeightForWidth())
        MainWindow.setSizePolicy(sizePolicy)
        MainWindow.setMinimumSize(QSize(640, 420))
        MainWindow.setMaximumSize(QSize(640, 420))
        MainWindow.setStyleSheet(u"/* Light, professional MainWindow style */\n"
"QMainWindow {\n"
"    background-color: #f7f8fa;       /* very light gray background */\n"
"    border: 1px solid #e0e0e0;       /* subtle border around window */\n"
"    border-radius: 10px;              /* slightly rounded corners */\n"
"}\n"
"\n"
"/* Optional: Central widget card effect */\n"
"QWidget#centralwidget {\n"
"    background-color: #ffffff;        /* white \u201ccard\u201d inside the window */\n"
"    border: 1px solid #dddddd;        /* light border for central widget */\n"
"    border-radius: 8px;\n"
"    padding: 8px;                     /* inner spacing */\n"
"}\n"
"\n"
"/* QPushButton style to match */\n"
"QPushButton {\n"
"    background-color: #ffffff;\n"
"    border: 1px solid #cccccc;\n"
"    border-radius: 6px;\n"
"    padding: 5px 12px;\n"
"    color: #333333;\n"
"    font-size: 11pt;\n"
"}\n"
"\n"
"QPushButton:hover {\n"
"    background-color: #e6f0ff;        /* subtle hover effect */\n"
"    border-color: #99c2ff;\n"
"}\n"
"\n"
"QPushBut"
                        "ton:pressed {\n"
"    background-color: #cce0ff;        /* pressed effect */\n"
"}")
        MainWindow.setUnifiedTitleAndToolBarOnMac(False)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.centralwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.tabWidget = QTabWidget(self.centralwidget)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tabWidget.setGeometry(QRect(0, 0, 651, 421))
        self.tabWidget.setTabShape(QTabWidget.TabShape.Rounded)
        self.logger_tab = QWidget()
        self.logger_tab.setObjectName(u"logger_tab")
        self.loggingTextBox = QTextEdit(self.logger_tab)
        self.loggingTextBox.setObjectName(u"loggingTextBox")
        self.loggingTextBox.setGeometry(QRect(10, 10, 621, 331))
        self.loggingTextBox.setStyleSheet(u"/* Light, professional log/output box style */\n"
"QTextEdit, QPlainTextEdit {\n"
"    /* Background & text */\n"
"    background-color: #ffffff;       /* white background */\n"
"    color: #333333;                  /* dark gray text */\n"
"    font-family: Arial, Helvetica, sans-serif;\n"
"    font-size: 11pt;\n"
"\n"
"    /* Border styling */\n"
"    border: 1.5px solid #cccccc;     /* light gray border */\n"
"    border-radius: 6px;\n"
"\n"
"    /* Inner padding */\n"
"    padding: 5px;\n"
"\n"
"    /* Text selection colors */\n"
"    selection-background-color: #cce4ff;\n"
"    selection-color: #000000;\n"
"}")
        self.loggingTextBox.setReadOnly(True)
        self.debug_level_comboBox = QComboBox(self.logger_tab)
        self.debug_level_comboBox.addItem("")
        self.debug_level_comboBox.addItem("")
        self.debug_level_comboBox.addItem("")
        self.debug_level_comboBox.addItem("")
        self.debug_level_comboBox.addItem("")
        self.debug_level_comboBox.setObjectName(u"debug_level_comboBox")
        self.debug_level_comboBox.setGeometry(QRect(520, 350, 101, 31))
        font = QFont()
        font.setPointSize(11)
        self.debug_level_comboBox.setFont(font)
        self.label_2 = QLabel(self.logger_tab)
        self.label_2.setObjectName(u"label_2")
        self.label_2.setGeometry(QRect(420, 350, 101, 31))
        self.label_2.setFont(font)
        self.label_3 = QLabel(self.logger_tab)
        self.label_3.setObjectName(u"label_3")
        self.label_3.setGeometry(QRect(175, 350, 41, 31))
        self.label_3.setFont(font)
        self.connectSocketButton = QPushButton(self.logger_tab)
        self.connectSocketButton.setObjectName(u"connectSocketButton")
        self.connectSocketButton.setEnabled(True)
        self.connectSocketButton.setGeometry(QRect(280, 350, 91, 31))
        font1 = QFont()
        font1.setPointSize(11)
        font1.setBold(False)
        font1.setItalic(False)
        font1.setKerning(True)
        self.connectSocketButton.setFont(font1)
        self.portLineEdit = QLineEdit(self.logger_tab)
        self.portLineEdit.setObjectName(u"portLineEdit")
        self.portLineEdit.setGeometry(QRect(210, 350, 51, 31))
        self.portLineEdit.setFont(font)
        self.ipLineEdit = QLineEdit(self.logger_tab)
        self.ipLineEdit.setObjectName(u"ipLineEdit")
        self.ipLineEdit.setGeometry(QRect(50, 350, 121, 31))
        self.ipLineEdit.setFont(font)
        self.label_4 = QLabel(self.logger_tab)
        self.label_4.setObjectName(u"label_4")
        self.label_4.setGeometry(QRect(10, 351, 41, 31))
        self.label_4.setFont(font)
        self.tabWidget.addTab(self.logger_tab, "")
        self.command_tab = QWidget()
        self.command_tab.setObjectName(u"command_tab")
        self.send_cmd_button = QPushButton(self.command_tab)
        self.send_cmd_button.setObjectName(u"send_cmd_button")
        self.send_cmd_button.setGeometry(QRect(420, 30, 141, 31))
        self.send_cmd_button.setFont(font)
        self.cmd_comboBox = QComboBox(self.command_tab)
        self.cmd_comboBox.addItem("")
        self.cmd_comboBox.addItem("")
        self.cmd_comboBox.addItem("")
        self.cmd_comboBox.setObjectName(u"cmd_comboBox")
        self.cmd_comboBox.setGeometry(QRect(101, 30, 171, 31))
        font2 = QFont()
        font2.setPointSize(12)
        self.cmd_comboBox.setFont(font2)
        self.label = QLabel(self.command_tab)
        self.label.setObjectName(u"label")
        self.label.setGeometry(QRect(10, 30, 91, 31))
        self.label.setFont(font2)
        self.label_8 = QLabel(self.command_tab)
        self.label_8.setObjectName(u"label_8")
        self.label_8.setGeometry(QRect(280, 30, 91, 31))
        self.label_8.setFont(font2)
        self.cmd_lineEdit = QLineEdit(self.command_tab)
        self.cmd_lineEdit.setObjectName(u"cmd_lineEdit")
        self.cmd_lineEdit.setGeometry(QRect(372, 34, 41, 21))
        self.cmd_lineEdit.setFont(font2)
        self.tabWidget.addTab(self.command_tab, "")
        self.status_tab = QWidget()
        self.status_tab.setObjectName(u"status_tab")
        self.tabWidget.addTab(self.status_tab, "")
        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

        self.tabWidget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Diagnose tool", None))
        self.loggingTextBox.setHtml(QCoreApplication.translate("MainWindow", u"<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0//EN\" \"http://www.w3.org/TR/REC-html40/strict.dtd\">\n"
"<html><head><meta name=\"qrichtext\" content=\"1\" /><meta charset=\"utf-8\" /><style type=\"text/css\">\n"
"p, li { white-space: pre-wrap; }\n"
"hr { height: 1px; border-width: 0; }\n"
"li.unchecked::marker { content: \"\\2610\"; }\n"
"li.checked::marker { content: \"\\2612\"; }\n"
"</style></head><body style=\" font-family:'Arial','Helvetica','sans-serif'; font-size:11pt; font-weight:400; font-style:normal;\">\n"
"<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" font-family:'Consolas','monospace'; font-size:12pt;\">12:55[Debug] Node 1 alive</span></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margi"
                        "n-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-par"
                        "agraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:1"
                        "2pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-fa"
                        "mily:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-"
                        "block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px;"
                        " margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:e"
                        "mpty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br />"
                        "</p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-family:'Consolas','monospace'; font-size:12pt;\"><br /></p></body></html>", None))
        self.debug_level_comboBox.setItemText(0, QCoreApplication.translate("MainWindow", u"TRACE", None))
        self.debug_level_comboBox.setItemText(1, QCoreApplication.translate("MainWindow", u"DEBUG", None))
        self.debug_level_comboBox.setItemText(2, QCoreApplication.translate("MainWindow", u"INFO", None))
        self.debug_level_comboBox.setItemText(3, QCoreApplication.translate("MainWindow", u"WARNING", None))
        self.debug_level_comboBox.setItemText(4, QCoreApplication.translate("MainWindow", u"ERROR", None))

        self.label_2.setText(QCoreApplication.translate("MainWindow", u"Debug Level:", None))
        self.label_3.setText(QCoreApplication.translate("MainWindow", u"Port:", None))
        self.connectSocketButton.setText(QCoreApplication.translate("MainWindow", u"Connect", None))
        self.portLineEdit.setText(QCoreApplication.translate("MainWindow", u"12345", None))
        self.ipLineEdit.setText(QCoreApplication.translate("MainWindow", u"255.255.255.255", None))
        self.label_4.setText(QCoreApplication.translate("MainWindow", u"IPv4:", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.logger_tab), QCoreApplication.translate("MainWindow", u"Logger", None))
        self.send_cmd_button.setText(QCoreApplication.translate("MainWindow", u"Send Command", None))
        self.cmd_comboBox.setItemText(0, QCoreApplication.translate("MainWindow", u"Print Recv Msg", None))
        self.cmd_comboBox.setItemText(1, QCoreApplication.translate("MainWindow", u"Induce Voting fault", None))
        self.cmd_comboBox.setItemText(2, QCoreApplication.translate("MainWindow", u"Induce CRC fault", None))

        self.label.setText(QCoreApplication.translate("MainWindow", u"Command:", None))
        self.label_8.setText(QCoreApplication.translate("MainWindow", u"Send to ID:", None))
        self.cmd_lineEdit.setText(QCoreApplication.translate("MainWindow", u"255", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.command_tab), QCoreApplication.translate("MainWindow", u"Command", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.status_tab), QCoreApplication.translate("MainWindow", u"Status", None))
    # retranslateUi

