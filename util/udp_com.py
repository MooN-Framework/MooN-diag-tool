from socket import *
from PySide6.QtCore import QThread, Signal

class UdpCom:
    def __init__(self, ip_address : str, port : int, broadcast_addr="255.255.255.255"):
        self.ip_address = ip_address
        self.port = port
        self.broadcast_addr = broadcast_addr
        self.udp_socket = socket(AF_INET, SOCK_DGRAM)
        self.udp_socket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)
        self.udp_socket.bind((self.ip_address, self.port))
        self.udp_socket.setblocking(False)
    
    def send_msg(self, msg_payload : str):
        self.udp_socket.sendto(msg_payload.encode("utf-8"), (self.broadcast_addr, self.port))

    def recv_msg(self) -> str:
        data, addr = self.udp_socket.recvfrom(4096)
        return data.decode("utf-8")
    
    def drop_socket(self):
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None

class UdpListener(QThread):
    message_received = Signal(str)

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
                self.msleep(50)
            except Exception as e:
                break

    def stop(self):
        self._running = False
        self.quit()
        self.wait()
