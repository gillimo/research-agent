import os
from typing import Dict, Any, Callable, Optional

AUTH_TOKEN_ENV = "LIBRARIAN_IPC_TOKEN"
ALLOWLIST_ENV = "LIBRARIAN_IPC_ALLOWLIST"
MAX_BYTES_ENV = "LIBRARIAN_IPC_MAX_BYTES"

try:
    from socketbridge.server import SocketServer as _BridgeServer
except Exception as exc:
    _BridgeServer = None
    _BRIDGE_IMPORT_ERROR = exc
else:
    _BRIDGE_IMPORT_ERROR = None


def _parse_allowlist(raw: str) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


class SocketServer:
    """SocketBridge-backed server for Librarian IPC messages."""

    def __init__(
        self,
        host: str,
        port: int,
        handler: Optional[Callable[[Dict[str, Any]], None]] = None,
        auth_token: Optional[str] = None,
        allowlist: Optional[list[str]] = None,
        max_bytes: Optional[int] = None,
        verbose: bool = False,
    ):
        if _BridgeServer is None:
            raise RuntimeError(
                "socketbridge is required for socket IPC; install it with `pip install socketbridge`"
            ) from _BRIDGE_IMPORT_ERROR
        token = auth_token if auth_token is not None else os.getenv(AUTH_TOKEN_ENV, "")
        allow = allowlist if allowlist is not None else _parse_allowlist(os.getenv(ALLOWLIST_ENV, ""))
        env_max = os.getenv(MAX_BYTES_ENV)
        max_payload = max_bytes if max_bytes is not None else int(env_max) if env_max else None
        self._server = _BridgeServer(
            host=host,
            port=port,
            handler=handler,
            auth_token=token,
            allowlist=allow,
            max_bytes=max_payload,
            verbose=verbose,
        )

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop()
