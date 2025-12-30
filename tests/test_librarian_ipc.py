import os
import time
import socket
from multiprocessing import Process

import pytest


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _run_librarian(port: int):
    os.environ["LIBRARIAN_HOST"] = "127.0.0.1"
    os.environ["LIBRARIAN_PORT"] = str(port)
    os.environ["RESEARCHER_CLOUD_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    from researcher.librarian import Librarian
    librarian = Librarian(debug_mode=False)
    librarian.run()


@pytest.fixture(scope="module")
def librarian_process():
    port = _find_free_port()
    p = Process(target=_run_librarian, args=(port,), daemon=True)
    p.start()
    time.sleep(1)
    yield ("127.0.0.1", port)
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=("127.0.0.1", port))
    client.shutdown()
    p.join(timeout=2)
    if p.is_alive():
        p.terminate()


def test_librarian_ipc_status(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.get_status()
    client.close()
    assert response is not None
    assert response.get("status") == "success"


def test_librarian_research_request(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.request_research("python pathlib basics")
    client.close()
    assert response is not None
    assert "status" in response
    assert "result" in response
