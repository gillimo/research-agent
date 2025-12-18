import re
from typing import Tuple

SK_RE = re.compile(r"sk-[A-Za-z0-9]{10,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]{3,}")


def sanitize_prompt(text: str) -> Tuple[str, bool]:
    """Sanitize obvious secrets/identifiers. Returns (sanitized, changed?)."""
    changed = False

    def repl_sk(match):
        nonlocal changed
        changed = True
        return "[REDACTED_KEY]"

    def repl_email(match):
        nonlocal changed
        changed = True
        return "[REDACTED_EMAIL]"

    def repl_path(match):
        nonlocal changed
        changed = True
        return "[REDACTED_PATH]"

    text2 = SK_RE.sub(repl_sk, text)
    text2 = EMAIL_RE.sub(repl_email, text2)
    text2 = PATH_RE.sub(repl_path, text2)
    return text2, changed
