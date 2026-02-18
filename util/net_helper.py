import ipaddress

def get_broadcast_address(ip_with_mask: str) -> str:
    net = ipaddress.ip_network(ip_with_mask, strict=False)
    return str(net.broadcast_address)

def is_valid_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
        return True
    except ipaddress.AddressValueError:
        return False