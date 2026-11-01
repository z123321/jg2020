import socket
import socks

socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", 7890)
socket.socket = socks.socksocket

import requests
resp = requests.get("https://www.google.com", timeout=10)
print("代理通了，状态码：", resp.status_code)
