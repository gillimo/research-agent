import os
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from multiprocessing.connection import Client, Listener # Using multiprocessing.connection for IPC

from researcher.state_manager import ROOT_DIR

# --- IPC Configuration ---
# For simplicity, using a file-based address for Unix domain socket or similar.
# This path needs to be consistent between client and librarian.
LIBRARIAN_IPC_ADDR = Path(os.getenv("LIBRARIAN_IPC_ADDR", ROOT_DIR / "ipc" / "librarian_socket"))
# For Windows, this might default to a (host, port) tuple if using TCP.
# For now, assuming Unix-like path for socket address.
# A more robust solution would detect OS and choose accordingly.
if os.name == 'nt': # Windows
    LIBRARIAN_IPC_TYPE = 'AF_INET'
    LIBRARIAN_IPC_HOST = 'localhost'
    LIBRARIAN_IPC_PORT = int(os.getenv("LIBRARIAN_IPC_PORT", 6000))
    LIBRARIAN_IPC_ADDR = (LIBRARIAN_IPC_HOST, LIBRARIAN_IPC_PORT)
else: # Unix-like
    LIBRARIAN_IPC_TYPE = 'AF_UNIX' # For Unix domain sockets
    # Ensure the directory for the socket exists
    LIBRARIAN_IPC_ADDR.parent.mkdir(parents=True, exist_ok=True)
    LIBRARIAN_IPC_ADDR = str(LIBRARIAN_IPC_ADDR) # Convert Path to str for Listener/Client

LIBRARIAN_IPC_TIMEOUT_S = int(os.getenv("LIBRARIAN_IPC_TIMEOUT_S", 10))
LIBRARIAN_IPC_RETRIES = int(os.getenv("LIBRARIAN_IPC_RETRIES", 3))
LIBRARIAN_IPC_RETRY_DELAY_S = float(os.getenv("LIBRARIAN_IPC_RETRY_DELAY_S", 0.5))

class LibrarianClient:
    """
    Client for communicating with the background Librarian process via IPC.
    """
    def __init__(self, address: Any = None, authkey: bytes = b'librarian_secret') -> None:
        self.address = address or LIBRARIAN_IPC_ADDR
        self.authkey = authkey
        self._conn: Optional[Client] = None

    def _connect(self) -> Optional[Client]:
        """Establishes a connection to the Librarian with retries."""
        for attempt in range(LIBRARIAN_IPC_RETRIES):
            try:
                # Listener/Client connection supports 'AF_UNIX' by passing a string path
                # or 'AF_INET' (TCP) by passing a (host, port) tuple.
                self._conn = Client(self.address, authkey=self.authkey)
                return self._conn
            except (FileNotFoundError, ConnectionRefusedError) as e:
                print(f"LibrarianClient: Connection failed (attempt {attempt+1}/{LIBRARIAN_IPC_RETRIES}): {e}")
                time.sleep(LIBRARIAN_IPC_RETRY_DELAY_S)
            except Exception as e:
                print(f"LibrarianClient: Unexpected error during connection: {e}")
                return None
        return None

    def _send_receive(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Sends a message to the Librarian and waits for a response."""
        if self._conn is None:
            if not self._connect():
                return {"status": "error", "message": "Failed to connect to Librarian."}
        
        try:
            self._conn.send(message)
            # Use poll to implement a timeout on receive
            if self._conn.poll(LIBRARIAN_IPC_TIMEOUT_S):
                response = self._conn.recv()
                return response
            else:
                self._conn.close() # Close connection if timeout
                self._conn = None
                return {"status": "error", "message": "Librarian response timed out."}
        except Exception as e:
            print(f"LibrarianClient: Error during IPC communication: {e}")
            self._conn.close() # Ensure connection is closed on error
            self._conn = None
            return {"status": "error", "message": f"IPC communication error: {e}"}

    def close(self) -> None:
        """Closes the connection to the Librarian."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def query_cloud(self, prompt: str, cloud_mode: str, cloud_cmd: Optional[str] = None, cloud_threshold: Optional[float] = None) -> Dict[str, Any]:
        """Sends a request to the Librarian to query a cloud LLM."""
        message = {
            "type": "cloud_query",
            "prompt": prompt,
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
        self.close() # Ensure client connection is closed after shutdown
        return response

if __name__ == "__main__":
    # Example usage for testing purposes
    print("--- LibrarianClient Test ---")
    
    # Ensure LIBRARIAN_IPC_ADDR is set correctly for your OS
    # For Unix-like: os.environ["LIBRARIAN_IPC_ADDR"] = "/tmp/librarian_socket"
    # For Windows (TCP): os.environ["LIBRARIAN_IPC_PORT"] = "6000"

    client = LibrarianClient()
    
    try:
        # Test basic status request (Librarian needs to be running)
        print("\nRequesting Librarian status...")
        status_resp = client.get_status()
        print(f"Status Response: {status_resp}")

        # Test cloud query (Librarian needs to be running and configured)
        print("\nSending cloud query request...")
        cloud_resp = client.query_cloud("What is the capital of France?", "auto")
        print(f"Cloud Query Response: {cloud_resp}")

        # Test ingestion request
        print("\nSending ingestion request...")
        ingest_resp = client.request_ingestion(["/tmp/test_doc.txt"])
        print(f"Ingestion Response: {ingest_resp}")

    except Exception as e:
        print(f"An error occurred during client test: {e}")
    finally:
        client.close()
        print("LibrarianClient test finished.")
