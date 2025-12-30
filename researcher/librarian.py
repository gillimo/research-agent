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


def _read_recent_gap_events(last_ts: str, limit: int = 200) -> List[Dict[str, Any]]:
    if not LEDGER_FILE.exists():
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
        self.state = load_state()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def _log(self, message: str, level: str = "info", **data: Any) -> None:
        event_data = {"component": "librarian", "message": message, **data}
        log_event(self.state, f"librarian_{level}", **event_data)
        if self.debug_mode:
            print(f"[Librarian {level.upper()}] {message} {json.dumps(data) if data else ''}")

    def _send_notification_to_researcher(self, notification: Dict[str, Any]):
        """Sends a notification to the researcher's socket server."""
        if not self.researcher_addr[0] or not self.researcher_addr[1]:
            self._log("Cannot send notification: researcher socket_server not configured.", level="warn")
            return
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
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
        self._log("Received IPC message", message_type=message.get("type"))
        response: Dict[str, Any] = {"status": "error", "message": "Unknown message type"}
        msg_type = message.get("type")

        if msg_type == "shutdown":
            self.running = False
            response = {"status": "success", "message": "Librarian shutting down"}
        elif msg_type == "status_request":
            response = {"status": "success", "message": "Librarian is running"}
        elif msg_type == "cloud_query":
            prompt = message.get("prompt", "")
            cmd_template = message.get("cloud_cmd")
            result: CloudCallResult = call_cloud(prompt=prompt, cmd_template=cmd_template)
            response = {"status": "success" if result.ok else "error", "result": result.__dict__}
        elif msg_type == "research_request":
            topic = message.get("topic", "").strip()
            intent = message.get("intent", "rag_update")
            if not topic:
                response = {"status": "error", "message": "Missing topic for research_request"}
            else:
                prompt = (
                    "You are the Librarian agent collaborating with Martin. "
                    "Provide a concise, public-sources summary for the following topic. "
                    "Include 3-5 bullet takeaways and suggested keywords for local RAG ingest. "
                    f"Topic: {topic}"
                )
                result = call_cloud(prompt=prompt, cmd_template=message.get("cloud_cmd"))
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
                response = {"status": "error", "message": "Missing topic for sources_request"}
            else:
                prompt = (
                    "You are the Librarian agent collaborating with Martin. "
                    "List 5-8 public sources (title + URL) that could be used to build a local RAG for this topic. "
                    "Keep each source on its own line with a short description. "
                    f"Topic: {topic}"
                )
                result = call_cloud(prompt=prompt, cmd_template=message.get("cloud_cmd"))
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
            if not text:
                response = {"status": "error", "message": "Missing text for ingest_text"}
            else:
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
                response = {"status": "success", "result": ingest_result}
                log_event(self.state, "librarian_ingest_text", topic=topic, source=source, hash=content_hash, path=str(path))
                self._send_notification_to_researcher({
                    "type": "notification",
                    "event": "ingestion_complete",
                    "details": {"source": source, "path": str(path), "hash": content_hash, "result": ingest_result},
                })
        elif msg_type == "ingest_request":
            paths_str: List[str] = message.get("paths", [])
            idx = load_index_from_config(self.cfg)
            files = [Path(p) for p in paths_str if Path(p).exists()]
            ingest_result = ingest_files(idx, files)
            if hasattr(idx, 'save'):
                idx.save()
            response = {"status": "success", "result": ingest_result}
            self._send_notification_to_researcher({"type": "notification", "event": "ingestion_complete", "details": ingest_result})

        self._log("Responding to IPC message", message_type=msg_type, response_status=response.get("status"))
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

                # Read message
                msg_bytes = b''
                while len(msg_bytes) < msg_len:
                    chunk = conn.recv(msg_len - len(msg_bytes))
                    if not chunk:
                        raise ConnectionError("Client closed connection unexpectedly.")
                    msg_bytes += chunk
                
                message = json.loads(msg_bytes.decode('utf-8'))
                
                # Process message and get response
                response_data = self._handle_ipc_message(message)

                # Send response
                resp_json = json.dumps(response_data).encode('utf-8')
                resp_len = struct.pack('!I', len(resp_json))
                conn.sendall(resp_len + resp_json)

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
        last_ts = self.state.get("librarian_last_gap_ts", "")
        gap_events = _read_recent_gap_events(last_ts)
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
                result = call_cloud(prompt=prompt, cmd_template=None)
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
