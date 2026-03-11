from PySide6.QtGui import QIntValidator, QIcon, QFont
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QGridLayout, QLabel
)

class LedIndicator(QLabel):
    def __init__(self, color="red", size_px=16):
        super().__init__()
        self._size = size_px
        self.color = color
        self.setFixedSize(self._size, self._size)
        self.update_style()

    def update_style(self):
        self.setStyleSheet(f"""
            background-color: {self.color};
            border-radius: {self._size // 2}px;
            border: 1px solid black;
        """)

    def set_color(self, color: str):
        self.color = color
        self.update_style()

class NodeStateManager:
    def __init__(self):
        self.system_id_row_map : dict = {}
        self.curr_row_index = 0
    
    def set_id_state(self, grid, id: int, state: str):
        if id not in self.system_id_row_map:
            return
        
        row = self.system_id_row_map[id]
        state_label_widget = grid.itemAtPosition(row, 1).widget()
        state_led_widget = grid.itemAtPosition(row, 2).widget()

        if state_label_widget:
            state_label_widget.setText("State: " + state)
        
        if state_led_widget:
            if state == "Failsafe":
                state_led_widget.set_color("red")
            elif state == "Error Handling":
                state_led_widget.set_color("yellow")
            else:
                state_led_widget.set_color("green")
                
    def add_status_row(self, grid, id):
        label = QLabel("SystemID: Node " + str(id))
        label2 = QLabel("State:")
        led = LedIndicator("green")
        font = QFont()
        font.setPointSize(11)  # 11 pt
        label.setFont(font)
        label2.setFont(font)
        grid.addWidget(label, self.curr_row_index, 0, alignment=Qt.AlignTop)
        grid.addWidget(label2, self.curr_row_index, 0 + 1, alignment=Qt.AlignTop)
        grid.addWidget(led, self.curr_row_index, 0 + 2, alignment=Qt.AlignTop)
        self.system_id_row_map[id] = self.curr_row_index
        self.curr_row_index += 1