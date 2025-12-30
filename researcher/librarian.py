import time
import os
import json
import socket
import struct
import threading
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from researcher.state_manager import load_state, log_event, ROOT_DIR, LEDGER_FILE
from researcher.librarian_client import LIBRARIAN_HOST, LIBRARIAN_PORT
from researcher.cloud_bridge import call_cloud, CloudCallResult
from researcher.ingester import ingest_files
from researcher.config_loader import load_config
from researcher.index_utils import load_index_from_config

LIBRARIAN_HEARTBEAT_INTERVAL_S = int(os.environ.get("LIBRARIAN_HEARTBEAT_INTERVAL_S", 10))
PROTOCOL_VERSION = "1"
AUTH_TOKEN_ENV = "LIBRARIAN_IPC_TOKEN"
ALLOWLIST_ENV = "LIBRARIAN_IPC_ALLOWLIST"
MAX_MSG_BYTES = int(os.environ.get("LIBRARIAN_IPC_MAX_BYTES", 1024 * 1024))
CHUNK_TTL_S = int(os.environ.get("LIBRARIAN_IPC_CHUNK_TTL_S", 300))
MAX_CHUNKS = int(os.environ.get("LIBRARIAN_IPC_MAX_CHUNKS", 200))
BACKOFF_BASE_S = float(os.environ.get("LIBRARIAN_BACKOFF_BASE_S", 2))
BACKOFF_MAX_S = float(os.environ.get("LIBRARIAN_BACKOFF_MAX_S", 60))
BREAKER_THRESHOLD = int(os.environ.get("LIBRARIAN_BREAKER_THRESHOLD", 3))
BREAKER_COOLDOWN_S = int(os.environ.get("LIBRARIAN_BREAKER_COOLDOWN_S", 300))


def _parse_allowlist(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _note_id(text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return h[:12]


def _parse_sources(text: str) -> List[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] in ("-", "*"):
            line = line[1:].strip()
        if len(line) >= 3 and line[:2].isdigit() and line[2:3] == ".":
            line = line[3:].strip()
        elif len(line) >= 2 and line[0].isdigit() and line[1:2] == ".":
            line = line[2:].strip()
        if line:
            lines.append(line)
    return lines[:10]


def _read_recent_gap_events(last_ts: str, limit: int = 200, cursor_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    if not LEDGER_FILE.exists():
        return []
    if cursor_path:
        try:
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor = {}
            if cursor_path.exists():
                cursor = json.loads(cursor_path.read_text(encoding="utf-8") or "{}")
            offset = int(cursor.get("offset", 0))
            size = LEDGER_FILE.stat().st_size
            if offset >= size:
                cursor_path.write_text(json.dumps({"offset": size, "last_ts": cursor.get("last_ts", "")}), encoding="utf-8")
                return []
            with LEDGER_FILE.open("r", encoding="utf-8") as f:
                f.seek(offset)
                lines = f.read().splitlines()
                new_offset = f.tell()
            events: List[Dict[str, Any]] = []
            last_seen = cursor.get("last_ts", "")
            for line in lines:
                try:
                    row = json.loads(line)
                    entry = row.get("entry", {})
                    if entry.get("event") != "rag_gap":
                        continue
                    ts = entry.get("ts", "")
                    if last_seen and ts <= last_seen:
                        continue
                    events.append(entry)
                except Exception:
                    continue
            new_last_ts = events[-1].get("ts", last_seen) if events else last_seen
            cursor_path.write_text(json.dumps({"offset": new_offset, "last_ts": new_last_ts}), encoding="utf-8")
            return events[-limit:]
        except Exception:
            return []
    try:
        lines = LEDGER_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    events: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
            entry = row.get("entry", {})
            if entry.get("event") != "rag_gap":
                continue
            ts = entry.get("ts", "")
            if last_ts and ts <= last_ts:
                continue
            events.append(entry)
        except Exception:
            continue
    return events


def _now_ts() -> float:
    return time.time()

class Librarian:
    """
    The background Librarian process. Manages cloud interactions, RAG upkeep,
    and listens for Researcher commands via a raw TCP socket.
    """
    def __init__(self, debug_mode: bool = False) -> None:
        self.cfg = load_config()
        server_cfg = self.cfg.get("socket_server", {})
        self.researcher_addr = (server_cfg.get("host"), server_cfg.get("port"))
        
        self.address = (LIBRARIAN_HOST, LIBRARIAN_PORT)
        self.debug_mode = debug_mode
        self.running = True
        self.last_upkeep_time = time.time()
        self.last_heartbeat_ts = _now_ts()
        self.last_request_ts = None
        self.state = load_state()
        self.allowlist = _parse_allowlist(os.environ.get(ALLOWLIST_ENV, ""))
        self._chunk_buffers: Dict[str, Dict[str, Any]] = {}
        self.cloud_failures = 0
        self.cloud_backoff_until = 0.0
        self.cloud_breaker_until = 0.0
        self.gap_cursor_path = ROOT_DIR / "logs" / "librarian_gap_cursor.json"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def _log(self, message: str, level: str = "info", **data: Any) -> None:
        event_data = {"component": "librarian", "message": message, **data}
        try:
            log_event(self.state, f"librarian_{level}", **event_data)
        except Exception:
            pass
        if self.debug_mode:
            print(f"[Librarian {level.upper()}] {message} {json.dumps(data) if data else ''}")

    def _call_cloud_with_policy(self, prompt: str, cmd_template: Optional[str]) -> CloudCallResult:
        now = _now_ts()
        if self.cloud_breaker_until and now < self.cloud_breaker_until:
            return CloudCallResult(False, "", "circuit breaker open", 1, "[blocked]", True, hashlib.sha256(b"breaker").hexdigest())
        if self.cloud_backoff_until and now < self.cloud_backoff_until:
            return CloudCallResult(False, "", "backoff active", 1, "[blocked]", True, hashlib.sha256(b"backoff").hexdigest())

        result = call_cloud(prompt=prompt, cmd_template=cmd_template)
        if result.ok:
            self.cloud_failures = 0
            self.cloud_backoff_until = 0.0
            return result

        self.cloud_failures += 1
        backoff = min(BACKOFF_BASE_S * (2 ** max(0, self.cloud_failures - 1)), BACKOFF_MAX_S)
        self.cloud_backoff_until = now + backoff
        self._log("Cloud backoff engaged", level="warn", failures=self.cloud_failures, backoff_s=backoff)
        if self.cloud_failures >= BREAKER_THRESHOLD:
            self.cloud_breaker_until = now + BREAKER_COOLDOWN_S
            self._log("Circuit breaker opened", level="warn", cooldown_s=BREAKER_COOLDOWN_S)
        return result

    def _handle_ingest_text(self, text: str, topic: str, source: str) -> Dict[str, Any]:
        if not text:
            return {"status": "error", "message": "Missing text for ingest_text", "code": "invalid_payload"}
        notes_dir = (ROOT_DIR / "data" / "processed" / "librarian_notes")
        notes_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        safe_topic = "".join(ch for ch in topic if ch.isalnum() or ch in ("-", "_")).strip() or "note"
        path = notes_dir / f"{ts}_{safe_topic}.txt"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        header = [
            f"# source: {source}",
            f"# topic: {topic}",
            f"# hash: {content_hash}",
            f"# ts: {ts}",
            "",
        ]
        path.write_text("\n".join(header) + text + "\n", encoding="utf-8")
        idx = load_index_from_config(self.cfg)
        ingest_result = ingest_files(idx, [path])
        if hasattr(idx, "save"):
            idx.save()
        log_event(self.state, "librarian_ingest_text", topic=topic, source=source, hash=content_hash, path=str(path))
        self._send_notification_to_researcher({
            "type": "notification",
            "event": "ingestion_complete",
            "details": {"source": source, "path": str(path), "hash": content_hash, "result": ingest_result},
        })
        return {"status": "success", "result": ingest_result}

    def _handle_ingest_chunk(self, message: Dict[str, Any]) -> Dict[str, Any]:
        request_id = message.get("request_id") or ""
        chunk = message.get("chunk", "")
        total_chunks = int(message.get("total_chunks") or 0)
        chunk_index = int(message.get("chunk_index") or 0)
        topic = message.get("topic", "").strip() or "librarian_note"
        source = message.get("source", "librarian_note")
        if not request_id or not chunk:
            return {"status": "error", "message": "Missing chunk payload", "code": "invalid_payload"}
        if total_chunks <= 0 or total_chunks > MAX_CHUNKS:
            return {"status": "error", "message": "Invalid total_chunks", "code": "invalid_payload"}
        if chunk_index < 0 or chunk_index >= total_chunks:
            return {"status": "error", "message": "Invalid chunk_index", "code": "invalid_payload"}

        buf = self._chunk_buffers.get(request_id)
        if not buf:
            buf = {
                "chunks": [None] * total_chunks,
                "topic": topic,
                "source": source,
                "created": _now_ts(),
            }
            self._chunk_buffers[request_id] = buf
        if total_chunks != len(buf["chunks"]):
            return {"status": "error", "message": "Chunk count mismatch", "code": "invalid_payload"}

        buf["chunks"][chunk_index] = chunk
        if all(part is not None for part in buf["chunks"]):
            text = "".join(part for part in buf["chunks"] if part)
            self._chunk_buffers.pop(request_id, None)
            return self._handle_ingest_text(text, buf["topic"], buf["source"])
        return {"status": "success", "message": "chunk_received"}

    def _cleanup_chunk_buffers(self) -> None:
        if not self._chunk_buffers:
            return
        now = _now_ts()
        expired = [k for k, v in self._chunk_buffers.items() if now - v.get("created", now) > CHUNK_TTL_S]
        for key in expired:
            self._chunk_buffers.pop(key, None)
        if expired:
            self._log("Expired chunk buffers", level="warn", count=len(expired))

    def _send_notification_to_researcher(self, notification: Dict[str, Any]):
        """Sends a notification to the researcher's socket server."""
        if not self.researcher_addr[0] or not self.researcher_addr[1]:
            self._log("Cannot send notification: researcher socket_server not configured.", level="warn")
            return
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                notification.setdefault("protocol_version", PROTOCOL_VERSION)
                auth_token = os.environ.get(AUTH_TOKEN_ENV, "")
                if auth_token:
                    notification.setdefault("auth_token", auth_token)
                sock.connect(self.researcher_addr)
                msg_json = json.dumps(notification).encode('utf-8')
                msg_len = struct.pack('!I', len(msg_json))
                sock.sendall(msg_len + msg_json)
                
                # Wait for acknowledgment
                resp_len_bytes = sock.recv(4)
                if resp_len_bytes:
                    resp_len = struct.unpack('!I', resp_len_bytes)[0]
                    response = sock.recv(resp_len)
                    self._log("Sent notification to researcher and received response.", notification=notification, response=response.decode("utf-8"))
        except ConnectionRefusedError:
            self._log("Could not connect to researcher's socket server. It may not be running.", level="warn", host=self.researcher_addr[0], port=self.researcher_addr[1])
        except Exception as e:
            self._log("Failed to send notification to researcher.", level="error", error=str(e))

    def _handle_ipc_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handles incoming IPC messages from the Researcher."""
        request_id = message.get("request_id")
        self._log("Received IPC message", message_type=message.get("type"), request_id=request_id)
        if message.get("protocol_version") != PROTOCOL_VERSION:
            return {
                "status": "error",
                "message": "Protocol version mismatch.",
                "code": "protocol_mismatch",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
            }
        response: Dict[str, Any] = {"status": "error", "message": "Unknown message type", "code": "unknown_message"}
        msg_type = message.get("type")
        self.last_request_ts = _now_ts()

        if msg_type == "shutdown":
            self.running = False
            response = {"status": "success", "message": "Librarian shutting down"}
        elif msg_type == "status_request":
            now = _now_ts()
            response = {
                "status": "success",
                "message": "Librarian is running",
                "heartbeat_ts": self.last_heartbeat_ts,
                "heartbeat_age_s": round(max(0.0, now - self.last_heartbeat_ts), 2),
                "last_request_ts": self.last_request_ts,
                "cloud_backoff_until": self.cloud_backoff_until,
                "cloud_breaker_until": self.cloud_breaker_until,
            }
        elif msg_type == "cloud_query":
            if message.get("sanitized") is not True:
                response = {"status": "error", "message": "sanitized prompt required", "code": "sanitize_required"}
            else:
                prompt = message.get("prompt", "")
                cmd_template = message.get("cloud_cmd")
                result: CloudCallResult = self._call_cloud_with_policy(prompt=prompt, cmd_template=cmd_template)
                response = {"status": "success" if result.ok else "error", "result": result.__dict__}
        elif msg_type == "research_request":
            topic = message.get("topic", "").strip()
            intent = message.get("intent", "rag_update")
            if not topic:
                response = {"status": "error", "message": "Missing topic for research_request", "code": "invalid_payload"}
            else:
                prompt = (
                    "You are the Librarian agent collaborating with Martin. "
                    "Provide a concise, public-sources summary for the following topic. "
                    "Include 3-5 bullet takeaways and suggested keywords for local RAG ingest. "
                    f"Topic: {topic}"
                )
                result = self._call_cloud_with_policy(prompt=prompt, cmd_template=message.get("cloud_cmd"))
                output_hash = hashlib.sha256((result.output or "").encode("utf-8")).hexdigest() if result.output else ""
                response = {"status": "success" if result.ok else "error", "result": result.__dict__}
                note = {
                    "type": "notification",
                    "event": "librarian_note",
                    "details": {
                        "note_id": _note_id(topic + (result.output or "")),
                        "topic": topic,
                        "intent": intent,
                        "ok": result.ok,
                        "hash": result.hash,
                        "output_hash": output_hash,
                        "summary": (result.output or "")[:2000],
                        "error": result.error,
                    },
                }
                self._send_notification_to_researcher(note)
        elif msg_type == "sources_request":
            topic = message.get("topic", "").strip()
            if not topic:
                response = {"status": "error", "message": "Missing topic for sources_request", "code": "invalid_payload"}
            else:
                prompt = (
                    "You are the Librarian agent collaborating with Martin. "
                    "List 5-8 public sources (title + URL) that could be used to build a local RAG for this topic. "
                    "Keep each source on its own line with a short description. "
                    f"Topic: {topic}"
                )
                result = self._call_cloud_with_policy(prompt=prompt, cmd_template=message.get("cloud_cmd"))
                response = {"status": "success" if result.ok else "error", "result": result.__dict__}
                sources = _parse_sources(result.output or "")
                note = {
                    "type": "notification",
                    "event": "librarian_sources",
                    "details": {
                        "note_id": _note_id(topic + (result.output or "")),
                        "topic": topic,
                        "ok": result.ok,
                        "hash": result.hash,
                        "sources_text": (result.output or "")[:4000],
                        "sources": sources,
                        "error": result.error,
                    },
                }
                self._send_notification_to_researcher(note)
        elif msg_type == "ingest_text":
            text = message.get("text", "").strip()
            topic = message.get("topic", "").strip() or "librarian_note"
            source = message.get("source", "librarian_note")
            response = self._handle_ingest_text(text, topic, source)
        elif msg_type == "ingest_text_chunk":
            response = self._handle_ingest_chunk(message)
        elif msg_type == "cancel_request":
            target = message.get("target_request_id") or message.get("request_id")
            if target and target in self._chunk_buffers:
                self._chunk_buffers.pop(target, None)
                response = {"status": "success", "message": "request canceled"}
            else:
                response = {"status": "error", "message": "request not found", "code": "not_found"}
        elif msg_type == "ingest_request":
            paths_str: List[str] = message.get("paths", [])
            idx = load_index_from_config(self.cfg)
            files = [Path(p) for p in paths_str if Path(p).exists()]
            ingest_result = ingest_files(idx, files)
            if hasattr(idx, 'save'):
                idx.save()
            response = {"status": "success", "result": ingest_result}
            self._send_notification_to_researcher({"type": "notification", "event": "ingestion_complete", "details": ingest_result})

        response["protocol_version"] = PROTOCOL_VERSION
        if request_id:
            response["request_id"] = request_id
        self._log("Responding to IPC message", message_type=msg_type, response_status=response.get("status"), request_id=request_id)
        return response
        
    def _handle_client(self, conn, addr):
        """Handle incoming client connection in a dedicated thread."""
        self._log(f"Accepted new connection from {addr}")
        try:
            while self.running:
                # Read message length
                len_bytes = conn.recv(4)
                if not len_bytes:
                    break
                msg_len = struct.unpack('!I', len_bytes)[0]
                if msg_len > MAX_MSG_BYTES:
                    # Drain oversized payload
                    remaining = msg_len
                    while remaining > 0:
                        chunk = conn.recv(min(4096, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                    response_data = {
                        "status": "error",
                        "message": "payload too large",
                        "code": "payload_too_large",
                        "protocol_version": PROTOCOL_VERSION,
                    }
                    resp_json = json.dumps(response_data).encode('utf-8')
                    resp_len = struct.pack('!I', len(resp_json))
                    conn.sendall(resp_len + resp_json)
                    continue

                # Read message
                msg_bytes = b''
                while len(msg_bytes) < msg_len:
                    chunk = conn.recv(msg_len - len(msg_bytes))
                    if not chunk:
                        raise ConnectionError("Client closed connection unexpectedly.")
                    msg_bytes += chunk
                
                start_ts = time.time()
                message = json.loads(msg_bytes.decode('utf-8'))
                if self.allowlist and addr[0] not in self.allowlist:
                    self._log("Rejected IPC client (allowlist)", level="warn", client=addr[0])
                    response_data = {
                        "status": "error",
                        "message": "Unauthorized host",
                        "code": "unauthorized_host",
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": message.get("request_id"),
                    }
                else:
                    auth_token = os.environ.get(AUTH_TOKEN_ENV, "")
                    if auth_token and message.get("auth_token") != auth_token:
                        response_data = {
                            "status": "error",
                            "message": "Unauthorized",
                            "code": "unauthorized",
                            "protocol_version": PROTOCOL_VERSION,
                            "request_id": message.get("request_id"),
                        }
                    else:
                        # Process message and get response
                        response_data = self._handle_ipc_message(message)
                
                # Send response
                resp_json = json.dumps(response_data).encode('utf-8')
                resp_len = struct.pack('!I', len(resp_json))
                conn.sendall(resp_len + resp_json)
                try:
                    duration_ms = int((time.time() - start_ts) * 1000)
                    log_event(
                        self.state,
                        "librarian_ipc",
                        request_id=message.get("request_id"),
                        message_type=message.get("type"),
                        status=response_data.get("status"),
                        code=response_data.get("code"),
                        msg_bytes=msg_len,
                        resp_bytes=len(resp_json),
                        duration_ms=duration_ms,
                    )
                except Exception:
                    pass

                if message.get("type") == "shutdown":
                    break
        except (ConnectionResetError, ConnectionAbortedError):
            self._log(f"Client {addr} disconnected.", level="warn")
        except Exception as e:
            self._log(f"Error handling client {addr}: {e}", level="error")
        finally:
            self._log(f"Closing connection from {addr}")
            conn.close()

    def _perform_upkeep(self) -> None:
        """Periodically sends a proactive message to the researcher."""
        self._log("Performing upkeep and sending proactive message.")
        self._cleanup_chunk_buffers()
        last_ts = self.state.get("librarian_last_gap_ts", "")
        gap_events = _read_recent_gap_events(last_ts, cursor_path=self.gap_cursor_path)
        for entry in gap_events:
            data = entry.get("data", {})
            topic = data.get("prompt", "")
            note = {
                "type": "notification",
                "event": "rag_gap",
                "details": {
                    "note_id": _note_id(topic + entry.get("ts", "")),
                    "prompt": topic,
                    "score": data.get("top_score"),
                    "suggestion": "Consider /librarian request <topic> or ingest more local sources.",
                },
            }
            self._send_notification_to_researcher(note)
            if self.cfg.get("auto_update", {}).get("sources_on_gap") and topic:
                prompt = (
                    "You are the Librarian agent collaborating with Martin. "
                    "List 3-5 public sources (title + URL) to build a local RAG for this topic. "
                    "Keep each source on its own line with a short description. "
                    f"Topic: {topic}"
                )
                result = self._call_cloud_with_policy(prompt=prompt, cmd_template=None)
                sources = _parse_sources(result.output or "")
                src_note = {
                    "type": "notification",
                    "event": "librarian_sources",
                    "details": {
                        "note_id": _note_id(topic + (result.output or "")),
                        "topic": topic,
                        "ok": result.ok,
                        "hash": result.hash,
                        "sources_text": (result.output or "")[:4000],
                        "sources": sources,
                        "error": result.error,
                    },
                }
                self._send_notification_to_researcher(src_note)
        if gap_events:
            self.state["librarian_last_gap_ts"] = gap_events[-1].get("ts", last_ts)
            try:
                from researcher.state_manager import save_state
                save_state(self.state)
            except Exception:
                pass
        self._send_notification_to_researcher({
            "type": "notification",
            "event": "heartbeat",
            "details": {"timestamp": time.time()}
        })
        self.last_heartbeat_ts = _now_ts()
        self.last_upkeep_time = time.time()

    def run(self) -> None:
        """Main loop for the Librarian background process."""
        self._log("Librarian starting up.")
        try:
            self.sock.bind(self.address)
            self.sock.listen(5)
            self._log(f"Librarian listening on {self.address[0]}:{self.address[1]}")
        except Exception as e:
            self._log(f"Failed to bind/listen: {e}", level="error")
            return

        while self.running:
            try:
                # Use a timeout on accept to allow checking self.running
                self.sock.settimeout(1.0)
                conn, addr = self.sock.accept()
                client_thread = threading.Thread(target=self._handle_client, args=(conn, addr))
                client_thread.daemon = True
                client_thread.start()
            except socket.timeout:
                continue # Go back to checking self.running
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                if self.running:
                    self._log(f"Error accepting connections: {e}", level="error")
                break
            
            if (time.time() - self.last_upkeep_time) >= LIBRARIAN_HEARTBEAT_INTERVAL_S:
                self._perform_upkeep()

        self.sock.close()
        self._log("Librarian shut down.")


def start_librarian_process(debug_mode: bool = False) -> None:
    """Function to start the Librarian process."""
    librarian = Librarian(debug_mode=debug_mode)
    librarian.run()

if __name__ == "__main__":
    print("--- Starting Librarian (Debug Mode) ---")
    start_librarian_process(debug_mode=True)
