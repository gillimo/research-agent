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
    os.environ["LIBRARIAN_IPC_TOKEN"] = "test-token"
    os.environ["LIBRARIAN_IPC_MAX_BYTES"] = "512"
    os.environ["LIBRARIAN_IPC_CHUNK_BYTES"] = "200"
    os.environ["LIBRARIAN_TIMEOUT_S"] = "60"
    os.environ["RESEARCHER_FORCE_SIMPLE_INDEX"] = "1"
    os.environ["LIBRARIAN_TOPIC_BLOCKLIST"] = "blockedtopic"
    os.environ["RESEARCHER_CLOUD_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    from researcher.librarian import Librarian
    librarian = Librarian(debug_mode=False)
    librarian.run()


@pytest.fixture(scope="module")
def librarian_process():
    port = _find_free_port()
    os.environ["LIBRARIAN_IPC_TOKEN"] = "test-token"
    os.environ["LIBRARIAN_IPC_MAX_BYTES"] = "512"
    os.environ["LIBRARIAN_IPC_CHUNK_BYTES"] = "200"
    os.environ["LIBRARIAN_TIMEOUT_S"] = "60"
    os.environ["RESEARCHER_FORCE_SIMPLE_INDEX"] = "1"
    os.environ["LIBRARIAN_TOPIC_BLOCKLIST"] = "blockedtopic"
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
    assert response.get("request_id") == client.last_request_id
    assert "heartbeat_age_s" in response
    assert response.get("last_request_ts") is not None


def test_librarian_research_request(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.request_research("python pathlib basics")
    client.close()
    assert response is not None
    assert "status" in response
    assert "result" in response
    assert response.get("request_id") == client.last_request_id


def test_librarian_blocked_topic(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.request_research("blockedtopic details")
    client.close()
    assert response is not None
    assert response.get("status") == "error"
    assert response.get("code") == "blocked_topic"


def test_librarian_cloud_query_requires_sanitized(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client._send_receive({"type": "cloud_query", "prompt": "hi", "sanitized": False})
    client.close()
    assert response is not None
    assert response.get("status") == "error"
    assert response.get("code") == "sanitize_required"


def test_librarian_cancel_request(librarian_process):
    address = librarian_process
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.cancel_request("missing-request")
    client.close()
    assert response is not None
    assert response.get("status") == "error"
    assert response.get("code") == "not_found"


def test_librarian_ipc_rejects_missing_token(librarian_process, monkeypatch):
    address = librarian_process
    monkeypatch.delenv("LIBRARIAN_IPC_TOKEN", raising=False)
    from researcher.librarian_client import LibrarianClient
    client = LibrarianClient(address=address)
    response = client.get_status()
    client.close()
    assert response is not None
    assert response.get("status") == "error"
    assert response.get("message") == "Unauthorized"
