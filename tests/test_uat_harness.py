import re

from scripts.uat_harness import _strip_ansi, _wait_for_prompt, _wait_for_prompt_text


def test_strip_ansi_removes_prompt_codes() -> None:
    text = "\x1b[94mYou:\x1b[0m "
    assert _strip_ansi(text).strip() == "You:"


def test_wait_for_prompt_matches_stripped_text() -> None:
    buffer = [_strip_ansi("\x1b[94mYou:\x1b[0m ")]
    prompt_regex = re.compile(r"\bYou:\s*$", re.IGNORECASE | re.MULTILINE)
    found, _ = _wait_for_prompt(buffer, prompt_regex, timeout=0.2)
    assert found


def test_wait_for_prompt_text_matches_prompt_event() -> None:
    events = [{"type": "prompt", "text": "\x1b[93mApprove running\x1b[0m "}]
    found, _ = _wait_for_prompt_text(events, ["Approve running"], timeout=0.2)
    assert found


def test_wait_for_prompt_text_advances_cursor() -> None:
    events = [
        {"type": "prompt", "text": "You: "},
        {"type": "prompt", "text": "Approve running these commands? "},
    ]
    found, cursor = _wait_for_prompt_text(events, ["You:"], timeout=0.2)
    assert found
    found, _ = _wait_for_prompt_text(events, ["Approve running"], timeout=0.2, cursor=cursor)
    assert found


def test_wait_for_prompt_text_ignores_non_prompt_events() -> None:
    events = [
        {"type": "output", "text": "You: "},
        {"type": "prompt", "text": "Approve running these commands? "},
    ]
    found, _ = _wait_for_prompt_text(events, ["Approve running"], timeout=0.2)
    assert found


def test_wait_for_prompt_text_times_out() -> None:
    events = [{"type": "prompt", "text": "You: "}]
    found, _ = _wait_for_prompt_text(events, ["Approve running"], timeout=0.1)
    assert not found


def test_wait_for_prompt_text_matches_any_token() -> None:
    events = [{"type": "prompt", "text": "Approve running these commands? "}]
    found, _ = _wait_for_prompt_text(events, ["You:", "Approve running"], timeout=0.2)
    assert found
