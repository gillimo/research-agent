import socket
import threading
import json
from typing import Dict, Any, Callable, Optional

class SocketServer:
    """
    A simple socket server to listen for messages from the Librarian,
    enabling bi-directional communication.
    """
    def __init__(self, host: str, port: int, handler: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.host = host
        self.port = port
        self.handler = handler
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_thread = None
        self._is_running = False

    def _handle_client(self, conn, addr):
        """Handle incoming client connection."""
        print(f"[SocketServer] Accepted connection from {addr}")
        try:
            while self._is_running:
                len_bytes = conn.recv(4)
                if not len_bytes:
                    break
                msg_len = int.from_bytes(len_bytes, byteorder="big")
                msg_bytes = b""
                while len(msg_bytes) < msg_len:
                    chunk = conn.recv(msg_len - len(msg_bytes))
                    if not chunk:
                        raise ConnectionError("Client closed connection unexpectedly.")
                    msg_bytes += chunk

                try:
                    message: Dict[str, Any] = json.loads(msg_bytes.decode("utf-8"))
                    print(f"[SocketServer] Received message: {message}")
                    if self.handler:
                        self.handler(message)
                    response = {"status": "ok", "message": "Message received"}
                except json.JSONDecodeError:
                    print(f"[SocketServer] Received non-JSON data: {msg_bytes.decode('utf-8')}")
                    response = {"status": "error", "message": "Invalid JSON format"}
                except Exception as e:
                    print(f"[SocketServer] Error handling message: {e}")
                    response = {"status": "error", "message": f"Handler error: {e}"}

                resp_json = json.dumps(response).encode("utf-8")
                resp_len = len(resp_json).to_bytes(4, byteorder="big")
                conn.sendall(resp_len + resp_json)

        finally:
            print(f"[SocketServer] Closing connection from {addr}")
            conn.close()

    def _run(self):
        """The main server loop."""
        self.sock.bind((self.host, self.port))
        self.sock.listen(1)
        print(f"[SocketServer] Listening on {self.host}:{self.port}")
        self._is_running = True

        while self._is_running:
            try:
                conn, addr = self.sock.accept()
                client_handler = threading.Thread(target=self._handle_client, args=(conn, addr))
                client_handler.daemon = True
                client_handler.start()
            except socket.error as e:
                # This can happen when the socket is closed while accept() is blocking
                if self._is_running:
                    print(f"[SocketServer] Socket error: {e}")
                break
        
        print("[SocketServer] Server loop has stopped.")

    def start(self):
        """Starts the socket server in a separate thread."""
        if self.server_thread is None or not self.server_thread.is_alive():
            self.server_thread = threading.Thread(target=self._run)
            self.server_thread.daemon = True
            self.server_thread.start()
            print("[SocketServer] Server started.")

    def stop(self):
        """Stops the socket server."""
        if self._is_running:
            self._is_running = False
            # To unblock the sock.accept(), we can connect to it ourselves
            try:
                # This is a common trick to unblock accept()
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect((self.host, self.port))
            except Exception as e:
                print(f"[SocketServer] Error while stopping server: {e}")
            
            self.sock.close()
            if self.server_thread:
                self.server_thread.join(timeout=2)
            print("[SocketServer] Server stopped.")

if __name__ == '__main__':
    # Example usage for testing
    server = SocketServer('127.0.0.1', 6001)
    server.start()
    try:
        # Keep the main thread alive
        while True:
            pass
    except KeyboardInterrupt:
        print("Shutting down server...")
        server.stop()
