from pathlib import Path

from researcher.tool_ledger import append_tool_entry, read_recent, export_json


def test_tool_ledger_append_and_read(tmp_path):
    ledger_path = tmp_path / "tool_ledger.ndjson"
    state = {"tool_ledger": {"entries": 0, "last_hash": None}}
    append_tool_entry(
        {
            "command": "echo hello",
            "cwd": str(tmp_path),
            "rc": 0,
            "ok": True,
            "duration_s": 0.1,
            "stdout": "hello",
            "stderr": "",
        },
        st=state,
        ledger_path=ledger_path,
    )
    rows = read_recent(limit=5, ledger_path=ledger_path)
    assert len(rows) == 1
    assert rows[0]["entry"]["rc"] == 0

    rows2 = read_recent(limit=5, ledger_path=ledger_path, filters={"rc_not": 0})
    assert rows2 == []


def test_tool_ledger_export(tmp_path):
    ledger_path = tmp_path / "tool_ledger.ndjson"
    out_path = tmp_path / "export.json"
    state = {"tool_ledger": {"entries": 0, "last_hash": None}}
    append_tool_entry(
        {
            "command": "echo hello",
            "cwd": str(tmp_path),
            "rc": 0,
            "ok": True,
            "duration_s": 0.1,
            "stdout": "hello",
            "stderr": "",
        },
        st=state,
        ledger_path=ledger_path,
    )
    export_json(out_path, ledger_path=ledger_path)
    assert out_path.exists()
