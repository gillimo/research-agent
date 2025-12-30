import os
import time
import json
import socket
import struct
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from researcher.state_manager import ROOT_DIR
from researcher import sanitize

# --- IPC Configuration ---
LIBRARIAN_HOST = os.getenv("LIBRARIAN_HOST", "127.0.0.1")
LIBRARIAN_PORT = int(os.getenv("LIBRARIAN_PORT", 6000))
LIBRARIAN_ADDR = (LIBRARIAN_HOST, LIBRARIAN_PORT)

LIBRARIAN_TIMEOUT_S = int(os.getenv("LIBRARIAN_TIMEOUT_S", 10))
LIBRARIAN_RETRIES = int(os.getenv("LIBRARIAN_RETRIES", 3))
LIBRARIAN_RETRY_DELAY_S = float(os.getenv("LIBRARIAN_RETRY_DELAY_S", 0.5))
PROTOCOL_VERSION = "1"

class LibrarianClient:
    """
    Client for communicating with the background Librarian process via raw TCP sockets.
    """
    def __init__(self, address: Tuple[str, int] = None) -> None:
        self.address = address or LIBRARIAN_ADDR
        self._conn: Optional[socket.socket] = None

    def _connect(self) -> bool:
        """Establishes a connection to the Librarian with retries."""
        if self._conn:
            return True
            
        for attempt in range(LIBRARIAN_RETRIES):
            try:
                self._conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._conn.settimeout(LIBRARIAN_TIMEOUT_S)
                self._conn.connect(self.address)
                return True
            except (ConnectionRefusedError, socket.timeout) as e:
                print(f"LibrarianClient: Connection failed (attempt {attempt+1}/{LIBRARIAN_RETRIES}): {e}")
                self._conn = None
                time.sleep(LIBRARIAN_RETRY_DELAY_S)
            except Exception as e:
                print(f"LibrarianClient: Unexpected error during connection: {e}")
                self._conn = None
                return False
        return False

    def _send_receive(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Sends a message to the Librarian and waits for a response."""
        if not self._connect():
            return {"status": "error", "message": "Failed to connect to Librarian."}
        
        try:
            message.setdefault("protocol_version", PROTOCOL_VERSION)
            # Encode message and prefix with its length (4-byte integer)
            msg_json = json.dumps(message).encode('utf-8')
            msg_len = struct.pack('!I', len(msg_json))
            self._conn.sendall(msg_len + msg_json)

            # Receive response length
            resp_len_bytes = self._conn.recv(4)
            if not resp_len_bytes:
                raise ConnectionError("Librarian closed the connection.")
            
            resp_len = struct.unpack('!I', resp_len_bytes)[0]
            
            # Receive response data
            response_bytes = b''
            while len(response_bytes) < resp_len:
                chunk = self._conn.recv(resp_len - len(response_bytes))
                if not chunk:
                    raise ConnectionError("Librarian closed the connection during response.")
                response_bytes += chunk

            response = json.loads(response_bytes.decode('utf-8'))
            if response.get("protocol_version") != PROTOCOL_VERSION:
                return {"status": "error", "message": "Protocol version mismatch."}
            return response

        except (socket.timeout, ConnectionError) as e:
            print(f"LibrarianClient: Communication error: {e}")
            self.close()
            return {"status": "error", "message": f"Communication error: {e}"}
        except Exception as e:
            print(f"LibrarianClient: Unexpected error during IPC communication: {e}")
            self.close()
            return {"status": "error", "message": f"IPC communication error: {e}"}

    def close(self) -> None:
        """Closes the connection to the Librarian."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def query_cloud(self, prompt: str, cloud_mode: str, cloud_cmd: Optional[str] = None, cloud_threshold: Optional[float] = None) -> Dict[str, Any]:
        """Sends a request to the Librarian to query a cloud LLM."""
        sanitized_prompt, changed = sanitize.sanitize_prompt(prompt or "")
        message = {
            "type": "cloud_query",
            "prompt": sanitized_prompt,
            "sanitized": True,
            "changed": changed,
            "cloud_mode": cloud_mode,
            "cloud_cmd": cloud_cmd,
            "cloud_threshold": cloud_threshold
        }
        return self._send_receive(message)

    def request_ingestion(self, paths: List[str]) -> Dict[str, Any]:
        """Sends a request to the Librarian to ingest files."""
        message = {
            "type": "ingest_request",
            "paths": paths
        }
        return self._send_receive(message)

    def request_card_catalog(self) -> Dict[str, Any]:
        """Sends a request to the Librarian to get the card catalog."""
        message = {
            "type": "get_card_catalog"
        }
        return self._send_receive(message)

    def request_research(self, topic: str, intent: str = "rag_update") -> Dict[str, Any]:
        """Ask the Librarian to research a topic and send back a note."""
        message = {
            "type": "research_request",
            "topic": topic,
            "intent": intent,
        }
        return self._send_receive(message)

    def ingest_text(self, text: str, topic: str = "", source: str = "librarian_note") -> Dict[str, Any]:
        """Send text content for the Librarian to ingest into the local RAG."""
        message = {
            "type": "ingest_text",
            "text": text,
            "topic": topic,
            "source": source,
        }
        return self._send_receive(message)

    def request_sources(self, topic: str) -> Dict[str, Any]:
        """Ask the Librarian for public source suggestions for a topic."""
        message = {
            "type": "sources_request",
            "topic": topic,
        }
        return self._send_receive(message)

    def get_status(self) -> Dict[str, Any]:
        """Requests status information from the Librarian."""
        message = {
            "type": "status_request"
        }
        return self._send_receive(message)

    def shutdown(self) -> Dict[str, Any]:
        """Sends a shutdown command to the Librarian."""
        message = {
            "type": "shutdown"
        }
        response = self._send_receive(message)
        self.close()
        return response

if __name__ == "__main__":
    # Example usage for testing purposes
    print("--- LibrarianClient Test (Raw Socket) ---")
    
    client = LibrarianClient()
    
    try:
        # Test basic status request (Librarian needs to be running)
        print("\nRequesting Librarian status...")
        status_resp = client.get_status()
        print(f"Status Response: {status_resp}")

    except Exception as e:
        print(f"An error occurred during client test: {e}")
    finally:
        client.close()
        print("LibrarianClient test finished.")
