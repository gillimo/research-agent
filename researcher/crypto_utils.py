from typing import Optional


def should_encrypt_logs(cfg, state) -> bool:
    trust = cfg.get("trust_policy", {}) or {}
    encrypt_logs = bool(trust.get("encrypt_logs", False))
    encrypt_when_remote = bool(trust.get("encrypt_logs_when_remote", True))
    current_host = ""
    if isinstance(state, dict):
        current_host = state.get("current_host", "") or ""
    if encrypt_when_remote and current_host and current_host != "local":
        encrypt_logs = True
    return encrypt_logs


def encrypt_text(text: str, key: str) -> str:
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError("cryptography not installed") from exc
    f = Fernet(key.encode("utf-8"))
    token = f.encrypt(text.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(token: str, key: str) -> str:
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError("cryptography not installed") from exc
    f = Fernet(key.encode("utf-8"))
    data = f.decrypt(token.encode("utf-8"))
    return data.decode("utf-8")


def generate_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError("cryptography not installed") from exc
    return Fernet.generate_key().decode("utf-8")
