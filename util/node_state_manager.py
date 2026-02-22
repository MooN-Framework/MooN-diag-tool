class LedIndicator(QLabel):
    def __init__(self, color="red", size=16):
        super().__init__()
        self.setFixedSize(size, size)
        self.setStyleSheet(f"""
            background-color: {color};
            border-radius: {size // 2}px;
            border: 1px solid black;
        """)

class node_state_manager:
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
