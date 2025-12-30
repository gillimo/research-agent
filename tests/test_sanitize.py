from researcher.sanitize import sanitize_prompt, scrub_data


def test_redacts_keys_and_emails_and_paths():
    raw = "here is sk-ABCDEF1234567890 and foo@example.com and C:\\secret\\file.txt"
    sanitized, changed = sanitize_prompt(raw)
    assert "[REDACTED_KEY]" in sanitized
    assert "[REDACTED_EMAIL]" in sanitized
    assert "[REDACTED_PATH]" in sanitized
    assert changed


def test_redacts_tokens_and_unix_paths():
    raw = "Bearer abc.def.ghi token=sekret /home/user/secret.txt"
    sanitized, changed = sanitize_prompt(raw)
    assert "Bearer [REDACTED_TOKEN]" in sanitized
    assert "token=[REDACTED]" in sanitized
    assert "[REDACTED_PATH]" in sanitized
    assert changed


def test_scrub_data_recursive():
    raw = {"auth": "Bearer abcdef123", "nested": {"path": "/etc/passwd"}}
    cleaned = scrub_data(raw)
    assert cleaned["auth"] == "Bearer [REDACTED_TOKEN]"
    assert cleaned["nested"]["path"] == "[REDACTED_PATH]"
