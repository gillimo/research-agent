import json
import socket
import time

from researcher.socket_server import SocketServer


def _send_message(host, port, payload, auth_token=None):
    if auth_token:
        payload = dict(payload)
        payload["auth_token"] = auth_token
    data = json.dumps(payload).encode("utf-8")
    size = len(data).to_bytes(4, byteorder="big")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, port))
        sock.sendall(size + data)
        resp_len_bytes = sock.recv(4)
        assert resp_len_bytes
        resp_len = int.from_bytes(resp_len_bytes, byteorder="big")
        resp = sock.recv(resp_len)
        return json.loads(resp.decode("utf-8"))


def test_socket_server_handler_receives_message(monkeypatch):
    monkeypatch.delenv("LIBRARIAN_IPC_TOKEN", raising=False)
    monkeypatch.delenv("LIBRARIAN_IPC_ALLOWLIST", raising=False)
    received = []

    def handler(msg):
        received.append(msg)

    server = SocketServer("127.0.0.1", 0, handler=handler)
    server.start()
    # Wait for server thread to bind
    time.sleep(0.1)
    host, port = server.sock.getsockname()

    resp = _send_message(host, port, {"type": "notification", "event": "test"})
    assert resp.get("status") == "ok"
    assert received and received[0]["event"] == "test"
    server.stop()


def test_socket_server_auth_rejects_missing_token(monkeypatch):
    monkeypatch.delenv("LIBRARIAN_IPC_ALLOWLIST", raising=False)
    received = []

    def handler(msg):
        received.append(msg)

    server = SocketServer("127.0.0.1", 0, handler=handler, auth_token="test-token")
    server.start()
    time.sleep(0.1)
    host, port = server.sock.getsockname()

    resp = _send_message(host, port, {"type": "notification", "event": "test"})
    assert resp.get("status") == "error"
    assert resp.get("message") == "Unauthorized"
    assert not received

    resp_ok = _send_message(host, port, {"type": "notification", "event": "test"}, auth_token="test-token")
    assert resp_ok.get("status") == "ok"
    assert received and received[0]["event"] == "test"
    server.stop()


def test_socket_server_allowlist_rejects_host(monkeypatch):
    monkeypatch.delenv("LIBRARIAN_IPC_TOKEN", raising=False)
    received = []

    def handler(msg):
        received.append(msg)

    server = SocketServer("127.0.0.1", 0, handler=handler, allowlist=["10.0.0.1"])
    server.start()
    time.sleep(0.1)
    host, port = server.sock.getsockname()

    resp = _send_message(host, port, {"type": "notification", "event": "test", "request_id": "req-1"})
    assert resp.get("status") == "error"
    assert resp.get("message") == "Unauthorized host"
    assert resp.get("request_id") == "req-1"
    assert not received
    server.stop()
