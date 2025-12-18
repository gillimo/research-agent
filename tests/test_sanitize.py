from researcher.sanitize import sanitize_prompt


def test_redacts_keys_and_emails_and_paths():
    raw = "here is sk-ABCDEF1234567890 and foo@example.com and C:\\secret\\file.txt"
    sanitized, changed = sanitize_prompt(raw)
    assert "[REDACTED_KEY]" in sanitized
    assert "[REDACTED_EMAIL]" in sanitized
    assert "[REDACTED_PATH]" in sanitized
    assert changed
