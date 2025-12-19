import pytest
import time
import os
import socket
from multiprocessing import Process

from researcher.librarian import Librarian
from researcher.librarian_client import LibrarianClient, LIBRARIAN_IPC_TYPE, LIBRARIAN_IPC_HOST

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

# Helper function to run the librarian in a separate process
def run_librarian(address):
    librarian = Librarian(address=address, debug_mode=True)
    librarian.run()

@pytest.fixture(scope="module")
def librarian_process():
    # Determine the address based on the OS
    if LIBRARIAN_IPC_TYPE == 'AF_INET':
        port = find_free_port()
        address = (LIBRARIAN_IPC_HOST, port)
    else:
        # For Unix-like systems, create a unique socket file path
        socket_dir = "/tmp"
        socket_file = f"librarian_test_{os.getpid()}.sock"
        address = os.path.join(socket_dir, socket_file)

    # Set up the librarian process
    p = Process(target=run_librarian, args=(address,), daemon=True)
    p.start()
    # Give the librarian a moment to start up
    time.sleep(1)
    yield address
    # Clean up the librarian process
    # Send a shutdown signal to the librarian
    client = LibrarianClient(address=address)
    client.shutdown()
    p.join(timeout=2)
    if p.is_alive():
        p.terminate()

    # Clean up the socket file if it exists
    if LIBRARIAN_IPC_TYPE == 'AF_UNIX' and os.path.exists(address):
        os.remove(address)


def test_librarian_ipc_status(librarian_process):
    """
    Tests that the LibrarianClient can connect to the Librarian and get a status response.
    """
    address = librarian_process
    client = LibrarianClient(address=address)
    response = client.get_status()
    client.close()

    assert response is not None
    assert response.get("status") == "success"
    assert "Librarian is running" in response.get("message", "")
