import re
from typing import Tuple

SK_RE = re.compile(r"sk-[A-Za-z0-9]{10,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]{3,}")
UNIX_PATH_RE = re.compile(r"(^|\s)(/[^\s]{2,})")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
AWS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
GH_TOKEN_RE = re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b")
SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
KV_SECRET_RE = re.compile(r"\b(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*([^\s'\";]+)", re.IGNORECASE)


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

    def repl_unix_path(match):
        nonlocal changed
        changed = True
        prefix = match.group(1) or ""
        return f"{prefix}[REDACTED_PATH]"

    def repl_bearer(match):
        nonlocal changed
        changed = True
        return "Bearer [REDACTED_TOKEN]"

    def repl_jwt(match):
        nonlocal changed
        changed = True
        return "[REDACTED_JWT]"

    def repl_key(match):
        nonlocal changed
        changed = True
        return "[REDACTED_KEY]"

    def repl_token(match):
        nonlocal changed
        changed = True
        return "[REDACTED_TOKEN]"

    def repl_kv_secret(match):
        nonlocal changed
        key = match.group(1)
        changed = True
        return f"{key}=[REDACTED]"

    text2 = SK_RE.sub(repl_sk, text)
    text2 = EMAIL_RE.sub(repl_email, text2)
    text2 = PATH_RE.sub(repl_path, text2)
    text2 = UNIX_PATH_RE.sub(repl_unix_path, text2)
    text2 = JWT_RE.sub(repl_jwt, text2)
    text2 = BEARER_RE.sub(repl_bearer, text2)
    text2 = AWS_KEY_RE.sub(repl_key, text2)
    text2 = GH_TOKEN_RE.sub(repl_token, text2)
    text2 = SLACK_TOKEN_RE.sub(repl_token, text2)
    text2 = KV_SECRET_RE.sub(repl_kv_secret, text2)
    return text2, changed


def scrub_data(value):
    if isinstance(value, str):
        return sanitize_prompt(value)[0]
    if isinstance(value, list):
        return [scrub_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub_data(v) for v in value)
    if isinstance(value, dict):
        return {k: scrub_data(v) for k, v in value.items()}
    return value
