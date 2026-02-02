import os
import json
import signal
import subprocess
from pathlib import Path
from typing import Dict, Any
import shutil

from researcher.config_loader import load_config
from researcher.state_manager import load_state


def _pid_path(cfg: Dict[str, Any]) -> Path:
    logs_dir = Path(cfg.get("data_paths", {}).get("logs", "logs"))
    return logs_dir / "remote_tunnel.json"


def _merge_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    st = load_state()
    current_host = st.get("current_host", "") if isinstance(st, dict) else ""
    overrides = st.get("remote_transport_overrides", {}) if isinstance(st, dict) else {}
    if current_host and isinstance(overrides, dict) and current_host in overrides:
        merged = dict(cfg)
        merged.update(overrides.get(current_host, {}) or {})
        return merged
    return cfg


def _build_ssh_args(cfg: Dict[str, Any]) -> Dict[str, Any]:
    rt = cfg.get("remote_transport", {}) or {}
    rt = _merge_overrides(rt)
    ssh_user = rt.get("ssh_user", "")
    ssh_host = rt.get("ssh_host", "")
    local_port = int(rt.get("local_port") or 6001)
    remote_port = int(rt.get("remote_port") or 6001)
    identity_file = rt.get("identity_file", "")
    if not ssh_host:
        return {"ok": False, "error": "missing ssh_host"}
    user_host = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
    args = ["ssh", "-N", "-L", f"{local_port}:127.0.0.1:{remote_port}", user_host]
    if identity_file:
        args = ["ssh", "-i", identity_file, "-N", "-L", f"{local_port}:127.0.0.1:{remote_port}", user_host]
    return {"ok": True, "args": args, "user_host": user_host}


def validate_transport(cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    rt = cfg.get("remote_transport", {}) or {}
    rt = _merge_overrides(rt)
    missing = []
    if (rt.get("type") or "ssh").lower() != "ssh":
        missing.append("type")
    if not rt.get("ssh_host"):
        missing.append("ssh_host")
    return {"ok": not missing, "missing": missing, "config": rt}


def start_tunnel(cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    rt = cfg.get("remote_transport", {}) or {}
    if (rt.get("type") or "ssh").lower() != "ssh":
        return {"ok": False, "error": "unsupported transport"}
    if not shutil.which("ssh"):
        return {"ok": False, "error": "ssh not found in PATH"}
    args_info = _build_ssh_args(cfg)
    if not args_info.get("ok"):
        return {"ok": False, "error": args_info.get("error", "invalid config")}
    pid_path = _pid_path(cfg)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.DETACHED_PROCESS
    proc = subprocess.Popen(args_info["args"], creationflags=creation_flags)
    payload = {"pid": proc.pid, "args": args_info["args"], "user_host": args_info["user_host"]}
    pid_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "pid": proc.pid}


def stop_tunnel(cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    pid_path = _pid_path(cfg)
    if not pid_path.exists():
        return {"ok": False, "error": "no pid file"}
    try:
        payload = json.loads(pid_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        payload = {}
    pid = payload.get("pid")
    if not pid:
        return {"ok": False, "error": "missing pid"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(int(pid), signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        return {"ok": True, "pid": pid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def status_tunnel(cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    pid_path = _pid_path(cfg)
    if not pid_path.exists():
        return {"ok": False, "status": "stopped"}
    try:
        payload = json.loads(pid_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        payload = {}
    pid = payload.get("pid")
    if not pid:
        return {"ok": False, "status": "stopped"}
    try:
        os.kill(int(pid), 0)
        return {"ok": True, "status": "running", "pid": pid, "details": payload}
    except Exception:
        return {"ok": False, "status": "stopped", "pid": pid}
