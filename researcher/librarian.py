import time
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from multiprocessing.connection import Listener, wait # New import for IPC listener

# Import state manager for logging, even in background process
from researcher.state_manager import load_state, log_event, ROOT_DIR
from researcher.librarian_client import LIBRARIAN_IPC_ADDR, LIBRARIAN_IPC_TYPE, LIBRARIAN_IPC_TIMEOUT_S # Import IPC config from client
from researcher.cloud_bridge import call_cloud, CloudCallResult # Import for cloud querying
from researcher.ingester import ingest_files # Import for ingestion
from researcher.config_loader import load_config # Import for configuration
from researcher.index_utils import load_index_from_config # Import for loading RAG index


# --- Librarian Configuration ---
LIBRARIAN_HEARTBEAT_INTERVAL_S = int(os.environ.get("LIBRARIAN_HEARTBEAT_INTERVAL_S", 10))
# LIBRARIAN_IPC_PATH = ROOT_DIR / "ipc" / "librarian_pipe" # No longer needed, using LIBRARIAN_IPC_ADDR from client


class Librarian:
    """
    The background Librarian process for the researcher agent.
    Manages cloud interactions, RAG upkeep, and listens for Researcher commands.
    """
    def __init__(self, address: Optional[Any] = None, debug_mode: bool = False, authkey: bytes = b'librarian_secret') -> None:
        self.address = address or LIBRARIAN_IPC_ADDR
        self.debug_mode = debug_mode
        self.running = True
        self.last_upkeep_time = time.time()
        self.state = load_state() # Load shared state for logging
        self.listener: Optional[Listener] = None
        self.authkey = authkey
        self.cfg = load_config() # Load researcher config for RAG and cloud settings
        self._setup_listener()

    def _setup_listener(self) -> None:
        """Sets up the IPC listener for Researcher communication."""
        try:
            # Clean up old socket if it exists (for Unix domain sockets)
            if LIBRARIAN_IPC_TYPE == 'AF_UNIX' and Path(self.address).exists():
                Path(self.address).unlink()
            
            self.listener = Listener(address=self.address, family=LIBRARIAN_IPC_TYPE, authkey=self.authkey)
            self._log("IPC Listener set up", address=str(self.address), type=LIBRARIAN_IPC_TYPE)
        except OSError as e:
            # Address already in use error (WSAEADDRINUSE)
            if e.winerror == 10048:
                self._log("Failed to set up IPC Listener: Address already in use.", level="error", error=str(e), address=str(self.address))
            else:
                self._log("Failed to set up IPC Listener due to OSError", level="error", error=str(e), address=str(self.address))
            self.running = False
        except Exception as e:
            self._log("Failed to set up IPC Listener", level="error", error=str(e), address=str(self.address))
            self.running = False # Cannot run without listener

    def _log(self, message: str, level: str = "info", **data: Any) -> None:
        """Helper to log events through the state manager."""
        event_data = {"component": "librarian", "message": message, **data}
        log_event(self.state, f"librarian_{level}", **event_data)
        if self.debug_mode:
            print(f"[Librarian {level.upper()}] {message} {json.dumps(data) if data else ''}")

    def _handle_ipc_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handles incoming IPC messages from the Researcher."""
        self._log("Received IPC message", message_type=message.get("type"), message=message)
        response: Dict[str, Any] = {"status": "error", "message": "Unknown message type"}

        msg_type = message.get("type")
        if msg_type == "shutdown":
            self.running = False
            response = {"status": "success", "message": "Librarian shutting down"}
        elif msg_type == "status_request":
            response = {
                "status": "success",
                "message": "Librarian is running",
                "uptime": time.time() - self.state.get("last_session", {}).get("started_at", time.time()),
                "last_upkeep": self.last_upkeep_time
            }
        elif msg_type == "cloud_query":
            prompt = message.get("prompt", "")
            # cloud_mode is from Researcher, Librarian always acts on demand.
            # No need for cloud_mode in call_cloud, just take the prompt.
            # cmd_template from message might override librarian's default if provided.
            cmd_template = message.get("cloud_cmd")
            # cloud_threshold from message not directly used here
            
            self._log("Processing cloud query", prompt_len=len(prompt))
            
            # Call cloud_bridge.call_cloud
            result: CloudCallResult = call_cloud(
                prompt=prompt,
                cmd_template=cmd_template, # cmd_template is passed from Researcher
                logs_root=Path(self.cfg.get("data_paths", {}).get("logs", "logs")) / "cloud", # Use config path
                timeout=self.cfg.get("cloud", {}).get("timeout", 60) # Use config timeout
            )
            response = {
                "status": "success" if result.ok else "error",
                "message": "Cloud query completed",
                "result": {
                    "ok": result.ok,
                    "output": result.output,
                    "error": result.error,
                    "rc": result.rc,
                    "sanitized": result.sanitized,
                    "changed": result.changed,
                    "hash": result.hash
                }
            }
        elif msg_type == "ingest_request":
            paths_str: List[str] = message.get("paths", [])
            if not paths_str:
                response = {"status": "error", "message": "No paths provided for ingestion."}
            else:
                self._log("Processing ingest request", paths_count=len(paths_str))
                # Load config dynamically to ensure it's fresh (or use self.cfg if updated periodically)
                # Using self.cfg for now, assuming it's up-to-date.
                idx = load_index_from_config(self.cfg)
                files = [Path(p) for p in paths_str if Path(p).exists()]
                ingest_result = ingest_files(idx, files)
                
                # Persist index after ingestion
                if hasattr(idx, 'save'): # Both SimpleIndex and FaissIndex have save()
                    idx.save()
                
                response = {
                    "status": "success",
                    "message": "Ingestion completed",
                    "result": {
                        "ingested": ingest_result.get("ingested", 0),
                        "errors": [str(e) for e in ingest_result.get("errors", [])]
                    }
                }
        elif msg_type == "get_card_catalog":
            self._log("Processing card catalog request")
            idx = load_index_from_config(self.cfg)
            
            catalog_data = {"total_docs": 0, "card_catalog": {}}
            if hasattr(idx, "meta") and idx.meta:
                all_paths = set()
                for meta_item in idx.meta:
                    if "path" in meta_item:
                        all_paths.add(meta_item["path"])

                catalog = {}
                ext_map = {
                    ".py": "PYTHON_SRC",
                    ".md": "MARKDOWN_DOCS",
                    ".txt": "TEXT_FILES",
                    ".json": "JSON_DATA",
                    ".yaml": "CONFIG_FILES",
                    ".yml": "CONFIG_FILES",
                }

                for path_str in sorted(list(all_paths)):
                    p = Path(path_str)
                    category = ext_map.get(p.suffix.lower(), "MISC")
                    if category not in catalog:
                        catalog[category] = {"count": 0, "files": []}
                    
                    catalog[category]["files"].append(path_str)
                    catalog[category]["count"] += 1
                
                catalog_data = {"total_docs": len(all_paths), "card_catalog": catalog}

            response = {
                "status": "success",
                "message": "Card catalog generated",
                "result": catalog_data
            }
        
        self._log("Responding to IPC message", message_type=msg_type, response_status=response.get("status"))
        return response

    def _perform_upkeep(self) -> None:
        """Placeholder for RAG upkeep tasks."""
        self._log("Performing upkeep")
        # This will be detailed in 'Implement Librarian Upkeep and RAG Management Functions' task.

    def _listen_for_ipc(self) -> None:
        """Listens for and handles incoming Researcher connections and messages."""
        if not self.listener:
            return

        conn = None
        try:
            # Use wait() to wait for a connection with a timeout
            if wait([self.listener], 0.1):
                conn = self.listener.accept()
                self._log("Accepted new IPC connection")
                msg = conn.recv()
                response = self._handle_ipc_message(msg)
                conn.send(response)
        except EOFError:
            self._log("IPC client disconnected unexpectedly", level="warn")
        except Exception as e:
            self._log("Error handling IPC connection", level="error", error=str(e))
        finally:
            if conn:
                conn.close()

    def run(self) -> None:
        """Main loop for the Librarian background process."""
        self._log("Librarian starting up.")
        # _ensure_ipc_dirs() is handled by librarian_client for AF_UNIX socket parent dir

        while self.running:
            try:
                self._listen_for_ipc() # Listen for messages

                # Perform upkeep tasks periodically
                if (time.time() - self.last_upkeep_time) >= LIBRARIAN_HEARTBEAT_INTERVAL_S:
                    self._perform_upkeep()
                    self.last_upkeep_time = time.time()
                
                # Heartbeat (logged by upkeep or if no upkeep, log here)
                self._log("Heartbeat")
                
                # Small sleep if no messages or upkeep to avoid busy-waiting.
                # sleep is implicit in listener.poll if no activity.
                time.sleep(1)

            except KeyboardInterrupt:
                self._log("Librarian received KeyboardInterrupt, shutting down.")
                self.running = False
            except Exception as e:
                self._log("Librarian encountered an unhandled error in main loop", level="error", error=str(e))
                # Consider if Librarian should crash or try to recover
                self.running = False # For now, critical error causes shutdown

        self._log("Librarian shutting down.")


def start_librarian_process(address: Optional[Any] = None, debug_mode: bool = False) -> None:
    """Function to start the Librarian process."""
    librarian = Librarian(address=address, debug_mode=debug_mode)
    librarian.run()

if __name__ == "__main__":
    # This block allows running the librarian directly for testing
    print("--- Starting Librarian (Debug Mode) ---")
    start_librarian_process(debug_mode=True)
