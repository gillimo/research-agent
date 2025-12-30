import json
import os
import queue
import socket
import threading
import time
from typing import Any, Dict, List, Optional


class _TeeStream:
    def __init__(self, original, bridge, stream_name: str) -> None:
        self._original = original
        self._bridge = bridge
        self._stream = stream_name

    def write(self, data: str) -> int:
        if not data:
            return 0
        written = self._original.write(data)
        self._bridge.send_event({"type": "output", "stream": self._stream, "text": data})
        return written

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self) -> int:
        return int(getattr(self._original, "fileno", lambda: -1)())


class TestSocketBridge:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7002,
        fallback_to_stdin: bool = False,
        token: Optional[str] = None,
        allow_non_loopback: bool = False,
        timeout_s: float = 0.0,
    ) -> None:
        self.host = host or "127.0.0.1"
        self.port = int(port or 7002)
        self.fallback_to_stdin = bool(fallback_to_stdin)
        self.token = token.strip() if isinstance(token, str) and token.strip() else None
        self.allow_non_loopback = bool(allow_non_loopback)
        self.timeout_s = float(timeout_s or 0.0)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._server: Optional[socket.socket] = None
        self._clients: List[socket.socket] = []
        self._client_lock = threading.Lock()
        self._running = threading.Event()
        self._server_thread: Optional[threading.Thread] = None
        self._orig_stdout = None
        self._orig_stderr = None
        self._last_prompt_at = 0.0
        self._last_prompt_text = ""
        self._last_phase_event: Optional[Dict[str, Any]] = None
        self._last_ready_event: Optional[Dict[str, Any]] = None

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(5)
        self._server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._server_thread.start()

    def stop(self) -> None:
        self._running.clear()
        with self._client_lock:
            for client in list(self._clients):
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        self.restore_streams()

    def install_streams(self) -> None:
        import sys
        if self._orig_stdout is None:
            self._orig_stdout = sys.stdout
        if self._orig_stderr is None:
            self._orig_stderr = sys.stderr
        sys.stdout = _TeeStream(self._orig_stdout, self, "stdout")
        sys.stderr = _TeeStream(self._orig_stderr, self, "stderr")

    def restore_streams(self) -> None:
        import sys
        if self._orig_stdout is not None:
            sys.stdout = self._orig_stdout
        if self._orig_stderr is not None:
            sys.stderr = self._orig_stderr

    def read_input(self, prompt: str = "") -> str:
        if prompt:
            self._emit_prompt(prompt)
        if self.fallback_to_stdin:
            import builtins
            return builtins.input("")
        try:
            if self.timeout_s and self.timeout_s > 0:
                text = self._queue.get(timeout=self.timeout_s)
            else:
                text = self._queue.get()
            self.send_event({"type": "input_used", "text": text})
            return text
        except queue.Empty:
            raise EOFError("test socket input timed out")

    def send_event(self, payload: Dict[str, Any]) -> None:
        if payload.get("type") == "phase":
            self._last_phase_event = payload
        if payload.get("type") in ("loop_ready", "input_wait"):
            self._last_ready_event = payload
        if os.environ.get("MARTIN_TEST_SOCKET_DEBUG") == "1":
            try:
                if self._orig_stdout:
                    self._orig_stdout.write(f"[test-socket] send {payload.get('type')}\n")
                    self._orig_stdout.flush()
            except Exception:
                pass
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        dead: List[socket.socket] = []
        with self._client_lock:
            for client in list(self._clients):
                try:
                    client.sendall(data.encode("utf-8"))
                except Exception:
                    dead.append(client)
            for client in dead:
                try:
                    client.close()
                except Exception:
                    pass
                if client in self._clients:
                    self._clients.remove(client)

    def _emit_prompt(self, prompt: str) -> None:
        self._last_prompt_at = time.time()
        self._last_prompt_text = prompt or ""
        if self._orig_stdout is not None and prompt:
            try:
                self._orig_stdout.write(prompt)
                self._orig_stdout.flush()
            except Exception:
                pass
        self.send_event({"type": "prompt", "text": prompt})

    def _accept_loop(self) -> None:
        while self._running.is_set():
            try:
                client, _addr = self._server.accept()
            except Exception:
                break
            if not self._allow_client(_addr):
                try:
                    client.close()
                except Exception:
                    pass
                continue
            with self._client_lock:
                self._clients.append(client)
            if self._last_prompt_text:
                try:
                    client.sendall(
                        (json.dumps({"type": "prompt", "text": self._last_prompt_text}, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                except Exception:
                    pass
            if self._last_phase_event:
                try:
                    client.sendall((json.dumps(self._last_phase_event, ensure_ascii=False) + "\n").encode("utf-8"))
                except Exception:
                    pass
            if self._last_ready_event:
                try:
                    client.sendall((json.dumps(self._last_ready_event, ensure_ascii=False) + "\n").encode("utf-8"))
                except Exception:
                    pass
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _allow_client(self, addr: Any) -> bool:
        try:
            host = addr[0]
        except Exception:
            return False
        if self.allow_non_loopback:
            return True
        return host in ("127.0.0.1", "::1")

    def _handle_client(self, client: socket.socket) -> None:
        buffer = ""
        try:
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if os.environ.get("MARTIN_TEST_SOCKET_DEBUG") == "1":
                        try:
                            if self._orig_stdout:
                                self._orig_stdout.write(f"[test-socket] recv {line}\n")
                                self._orig_stdout.flush()
                        except Exception:
                            pass
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    msg_type = payload.get("type")
                    if msg_type == "input":
                        token = payload.get("token")
                        if self.token and token != self.token:
                            self.send_event({"type": "auth_error", "text": "test socket token mismatch"})
                            continue
                        text = payload.get("text")
                        if isinstance(text, str):
                            self._queue.put(text)
                            self.send_event({"type": "input_ack", "text": text})
                    elif msg_type == "ping":
                        self.send_event({"type": "pong"})
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
