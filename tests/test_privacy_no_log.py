from researcher import state_manager as sm
from researcher import tool_ledger as tl


def test_log_event_respects_privacy_no_log(tmp_path, monkeypatch):
    st = sm.DEFAULT_STATE.copy()
    st["session_privacy"] = "no-log"
    st["ledger"] = {"entries": 0, "last_hash": None}
    monkeypatch.setattr(sm, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(sm, "LEDGER_FILE", tmp_path / "logs" / "ledger.ndjson")

    sm.log_event(st, "test_event", foo="bar")

    assert not sm.LEDGER_FILE.exists()


def test_tool_ledger_respects_privacy_no_log(tmp_path):
    st = {"session_privacy": "no-log", "tool_ledger": {"entries": 0, "last_hash": None}}
    path = tmp_path / "tool_ledger.ndjson"

    tl.append_tool_entry({"command": "echo hi"}, st=st, ledger_path=path)

    assert not path.exists()
