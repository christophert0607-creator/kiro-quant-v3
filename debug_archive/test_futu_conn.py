import socket
import sys

def test_conn(host, port):
    try:
        s = socket.create_connection((host, port), timeout=5)
        print(f"Successfully connected to {host}:{port}")
        s.close()
        return True
    except Exception as e:
        print(f"Failed to connect to {host}:{port}: {e}")
        return False

if __name__ == "__main__":
    test_conn("127.0.0.1", 11112)
