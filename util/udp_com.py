from socket import *

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