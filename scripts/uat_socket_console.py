import argparse
import json
import socket
import sys
import threading
import time


def _read_loop(sock: socket.socket) -> None:
    buffer = ""
    while True:
        try:
            chunk = sock.recv(4096)
        except Exception:
            break
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="ignore")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            msg_type = payload.get("type")
            text = payload.get("text") or ""
            if msg_type in ("output", "prompt"):
                sys.stdout.write(text)
                sys.stdout.flush()


def _send(sock: socket.socket, text: str, token: str) -> None:
    payload = {"type": "input", "text": text}
    if token:
        payload["token"] = token
    try:
        sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive test socket console for Martin UAT.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    try:
        sock = socket.create_connection((args.host, args.port), timeout=5.0)
    except Exception as exc:
        print(f"[error] Could not connect to test socket: {exc}", file=sys.stderr)
        return 2
    try:
        sock.settimeout(None)
    except Exception:
        pass

    reader = threading.Thread(target=_read_loop, args=(sock,), daemon=True)
    reader.start()

    try:
        sock.sendall((json.dumps({"type": "ping"}, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception:
        pass

    print("[uat] Connected. Type inputs; Ctrl+C to quit.")
    try:
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line:
                continue
            _send(sock, line, args.token)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    try:
        sock.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
