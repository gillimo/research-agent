from researcher.command_utils import classify_command_risk


def test_high_risk_fragments():
    risk = classify_command_risk("rm -rf /tmp/test")
    assert risk["level"] == "high"


def test_denylist_blocks():
    risk = classify_command_risk("echo hi", denylist=["echo"])
    assert risk["level"] == "blocked"


def test_allowlist_overrides():
    risk = classify_command_risk("rm -rf /tmp/test", allowlist=["rm -rf"])
    assert risk["level"] == "low"


def test_overwrite_detection(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("hi", encoding="utf-8")
    risk = classify_command_risk(f'echo hi > "{target}"')
    assert risk["level"] in ("medium", "high")
    assert any(r.startswith("overwrite:") for r in risk["reasons"])
