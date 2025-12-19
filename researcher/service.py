import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from researcher.config_loader import load_config, ensure_dirs
from researcher.cli import cmd_ask, cmd_ingest, get_status_payload


class _Handler(BaseHTTPRequestHandler):
    server_version = "researcher/0.1"

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        data = self.rfile.read(length) if length > 0 else b""
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/status":
            cfg = load_config()
            payload = get_status_payload(cfg, force_simple=False)
            self._send_json(200, payload)
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        cfg = load_config()
        ensure_dirs(cfg)
        if self.path.rstrip("/") == "/ask":
            data = self._read_json()
            prompt = str(data.get("prompt", "") or "")
            k = int(data.get("k", 5))
            use_llm = bool(data.get("use_llm", False))
            cloud_mode = str(data.get("cloud_mode", "off"))
            cloud_cmd = str(data.get("cloud_cmd", ""))
            cloud_threshold = data.get("cloud_threshold", None)
            force_simple = bool(data.get("simple_index", False))
            rc = cmd_ask(
                cfg,
                prompt,
                k,
                use_llm=use_llm,
                cloud_mode=cloud_mode,
                cloud_cmd=cloud_cmd,
                cloud_threshold=cloud_threshold,
                force_simple=force_simple,
            )
            self._send_json(200, {"ok": rc == 0})
            return
        if self.path.rstrip("/") == "/ingest":
            data = self._read_json()
            files = data.get("files", [])
            if not isinstance(files, list):
                files = []
            force_simple = bool(data.get("simple_index", False))
            rc = cmd_ingest(cfg, [str(p) for p in files], force_simple=force_simple)
            self._send_json(200, {"ok": rc == 0})
            return
        self._send_json(404, {"error": "not_found"})


def run_server(host: str = "127.0.0.1", port: int = 8088) -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"martin service listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
