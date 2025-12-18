import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from researcher import sanitize
from researcher.log_utils import setup_logger, log_event


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def allow_prompt(prompt: str) -> bool:
    """Simple allowlist: block obvious command execution hints."""
    lowered = prompt.lower()
    blocked = any(token in lowered for token in ["command:", "rm ", "del ", "format c:"])
    return not blocked


@dataclass
class CloudCallResult:
    ok: bool
    output: str
    error: str
    rc: int
    sanitized: str
    changed: bool
    hash: str


def call_cloud(prompt: str, cmd_template: str, logs_root: Path, timeout: int = 60) -> CloudCallResult:
    """Run a cloud CLI command with sanitized prompt and structured logging."""
    logs_root.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(logs_root / "cloud.log", name="researcher.cloud")

    sanitized, changed = sanitize.sanitize_prompt(prompt)
    if not allow_prompt(sanitized):
        log_event(logger, "cloud blocked allowlist")
        return CloudCallResult(False, "", "blocked by allowlist", 1, sanitized, changed, _hash(sanitized))

    if not cmd_template:
        log_event(logger, "cloud missing cmd_template")
        return CloudCallResult(False, "", "no cloud command template provided", 1, sanitized, changed, _hash(sanitized))

    hashed = _hash(sanitized)
    log_event(logger, f"cloud start hash={hashed} redacted={changed}")
    cmd = cmd_template.replace("{prompt}", sanitized)
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout.strip()
        error = proc.stderr.strip()
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        output, error, rc = "", "cloud command timed out", 124
    log_event(logger, f"cloud end hash={hashed} rc={rc}")
    return CloudCallResult(rc == 0, output, error, rc, sanitized, changed, hashed)
