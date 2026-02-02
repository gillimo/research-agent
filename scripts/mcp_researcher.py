#!/usr/bin/env python
"""
Minimal MCP stdio server for Researcher tools (ask/ingest/status/librarian status).
Supports JSON-RPC over stdio with either Content-Length framing or newline-delimited JSON.
"""
import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional


PROTOCOL_VERSION = "2024-11-05"


def _read_message() -> Optional[Dict[str, Any]]:
    buf = sys.stdin.buffer
    line = buf.readline()
    if not line:
        return None
    if line.startswith(b"Content-Length:"):
        headers = [line]
        while True:
            h = buf.readline()
            if not h:
                return None
            headers.append(h)
            if h in (b"\r\n", b"\n"):
                break
        length = 0
        for h in headers:
            if h.lower().startswith(b"content-length:"):
                try:
                    length = int(h.split(b":", 1)[1].strip())
                except Exception:
                    length = 0
        if length <= 0:
            return None
        body = buf.read(length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))
    try:
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None


def _send_message(payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: " + str(len(data)).encode("utf-8") + b"\r\n\r\n")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _cli_run(args: list[str], stdin_text: Optional[str] = None) -> Dict[str, Any]:
    cmd = [sys.executable, "-m", "researcher"] + args
    proc = subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
    )
    return {"rc": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def _tool_ask(params: Dict[str, Any]) -> Dict[str, Any]:
    prompt = (params.get("prompt") or params.get("query") or "").strip()
    if not prompt:
        return {"rc": 1, "stdout": "", "stderr": "missing prompt"}
    args = ["ask", "--json"]
    k = params.get("k")
    if isinstance(k, int) and k > 0:
        args += ["-k", str(k)]
    if params.get("use_llm"):
        args.append("--use-llm")
    cloud_mode = params.get("cloud_mode")
    if cloud_mode in ("off", "auto", "always"):
        args += ["--cloud-mode", cloud_mode]
    if params.get("simple_index"):
        args.append("--simple-index")
    args.append(prompt)
    return _cli_run(args)


def _tool_ingest(params: Dict[str, Any]) -> Dict[str, Any]:
    paths = params.get("paths") or params.get("files") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return {"rc": 1, "stdout": "", "stderr": "missing paths"}
    args = ["ingest", "--json"]
    exts = params.get("ext")
    if isinstance(exts, str) and exts.strip():
        args += ["--ext", exts]
    max_files = params.get("max_files")
    if isinstance(max_files, int) and max_files > 0:
        args += ["--max-files", str(max_files)]
    if params.get("simple_index"):
        args.append("--simple-index")
    args += [str(p) for p in paths]
    return _cli_run(args)


def _tool_status(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["status", "--json"]
    if params.get("simple_index"):
        args.append("--simple-index")
    return _cli_run(args)


def _tool_librarian_status(_params: Dict[str, Any]) -> Dict[str, Any]:
    return _cli_run(["librarian", "status"])


TOOLS = [
    {
        "name": "ask",
        "description": "Query the local RAG index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "k": {"type": "integer"},
                "use_llm": {"type": "boolean"},
                "cloud_mode": {"type": "string", "enum": ["off", "auto", "always"]},
                "simple_index": {"type": "boolean"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "ingest",
        "description": "Ingest files into the local RAG index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "ext": {"type": "string"},
                "max_files": {"type": "integer"},
                "simple_index": {"type": "boolean"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "status",
        "description": "Show Researcher status/config summary.",
        "inputSchema": {
            "type": "object",
            "properties": {"simple_index": {"type": "boolean"}},
        },
    },
    {
        "name": "librarian_status",
        "description": "Check Librarian status (if running).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = request.get("method")
    req_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "researcher-mcp", "version": "0.1"},
            "capabilities": {"tools": {}},
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if method in ("tools/list", "listTools"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method in ("tools/call", "callTool"):
        params = request.get("params", {}) or {}
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name == "ask":
            res = _tool_ask(args)
        elif name == "ingest":
            res = _tool_ingest(args)
        elif name == "status":
            res = _tool_status(args)
        elif name == "librarian_status":
            res = _tool_librarian_status(args)
        else:
            res = {"rc": 1, "stdout": "", "stderr": f"unknown tool: {name}"}
        text = res.get("stdout") or res.get("stderr") or ""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": bool(res.get("rc")),
            },
        }
    return None


def main() -> int:
    while True:
        req = _read_message()
        if req is None:
            break
        resp = _handle_request(req)
        if resp:
            _send_message(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
