from datetime import datetime

class LogParseManager:
    COLORS = {
        "INFO": "#2196F3",
        "SUCCESS": "#4CAF50",
        "WARNING": "#FFC107",
        "ERROR": "#F44336",
        "MASTER": "#9C27B0",
        "NODE": "#333333",
    }

    def __init__(self):
        pass

    def parse_generic_log(self, log : str) -> str:
        now = datetime.now().strftime("%H:%M:%S")
        splitted_log = log.split(":")
        fmt_log = log
        color = self.COLORS["ERROR"]
        match splitted_log[0]:
            case "MASTER":
                fmt_log = self.parse_master_msg(splitted_log)
                color = self.COLORS["MASTER"]
            case "SYSTEM":
                fmt_log = self.parse_system_msg(splitted_log)
                return
            case "LOG":
                fmt_log = self.parse_system_log(splitted_log)
                color = self.COLORS["NODE"]
        
        ret_str = (
            f'<span style="color:gray;">[{now}]</span> '
            f'<span style="color:{color};">{fmt_log}</span>'
        )
        return ret_str

    def parse_master_msg(self, splitted_log: str) -> str:
        return "[MASTER] To [NODE" + splitted_log[1]  + "] Execute " + self.int_to_cmd_str(int(splitted_log[3]))

    def parse_system_msg(self, splitted_log) -> str:
        if len(splitted_log) == 3:
            return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "]"
        elif len(splitted_log) == 4:
            return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "] Value(" + hex(int(splitted_log[3])) + ")" 
        
    def parse_system_log(self, splitted_log: str) -> str:
        return "[NODE" + splitted_log[1] + "] [" + splitted_log[2] + "] " + splitted_log[3]
    
    def int_to_cmd_str(self, cmd_int : int) -> str:
        match cmd_int:
            case 0:
                cmd_str = "Print Recv Msg"
            case 1:
                cmd_str = "Induce CRC fault"
            case 2:
                cmd_str = "Induce Voting fault"
        return cmd_str