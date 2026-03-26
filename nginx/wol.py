import os
import socket
from dotenv import load_dotenv

# Load env file relative to wol.py
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "mcweb.env"))

MAC = os.getenv("SERVERMAC")

def send_wol(mac):
    mac_bytes = bytes.fromhex(mac.replace(":", ""))
    packet = b'\xff' * 6 + mac_bytes * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(packet, ('<broadcast>', 9))
    sock.close()

if __name__ == "__main__":
    if MAC:
        print(f"Sending WOL to {MAC}")
        send_wol(MAC)
    else:
        print("SERVERMAC not set in mcweb.env")
