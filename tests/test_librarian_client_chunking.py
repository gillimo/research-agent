import importlib


def test_librarian_client_chunking(monkeypatch):
    monkeypatch.setenv("LIBRARIAN_IPC_MAX_BYTES", "200")
    monkeypatch.setenv("LIBRARIAN_IPC_CHUNK_BYTES", "50")

    import researcher.librarian_client as lc
    importlib.reload(lc)

    sent = []

    def fake_send(self, message):
        sent.append(message)
        return {"status": "success"}

    monkeypatch.setattr(lc.LibrarianClient, "_send_receive", fake_send)
    client = lc.LibrarianClient(address=("127.0.0.1", 0))
    resp = client.ingest_text("x" * 500, topic="topic", source="source")

    assert resp.get("status") == "success"
    assert len(sent) == 10
    assert all(m.get("type") == "ingest_text_chunk" for m in sent)
