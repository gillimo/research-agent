from researcher.martin_behaviors import extract_commands, sanitize_and_extract


def test_extract_commands_handles_cd_and_commands():
    text = """command: cd /tmp
command: ls -l
command: echo hi"""
    cmds = extract_commands(text)
    assert cmds == ["cd /tmp", "cd /tmp && ls -l", "cd /tmp && echo hi"]


def test_sanitize_and_extract_redacts():
    text = "command: echo sk-SECRETKEY1234"
    sanitized, changed, cmds = sanitize_and_extract(text)
    assert "[REDACTED_KEY]" in sanitized
    assert changed
    assert "SECRETKEY" not in cmds[0]
