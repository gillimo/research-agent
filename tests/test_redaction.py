from researcher import sanitize


def test_redact_api_key():
    text = "key sk-1234567890ABCDEF"
    cleaned, changed = sanitize.sanitize_prompt(text)
    assert "[REDACTED_KEY]" in cleaned
    assert changed


def test_redact_email():
    text = "contact me at test@example.com"
    cleaned, changed = sanitize.sanitize_prompt(text)
    assert "[REDACTED_EMAIL]" in cleaned
    assert changed


def test_redact_windows_path():
    text = "open C:\\Users\\alice\\secret.txt now"
    cleaned, changed = sanitize.sanitize_prompt(text)
    assert "[REDACTED_PATH]" in cleaned
    assert changed
