import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_scenario(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise SystemExit(f"Failed to load scenario JSON: {path} ({exc})")


def _build_command(entry: Optional[str]) -> List[str]:
    if entry:
        return entry.split()
    return [sys.executable, "-m", "researcher.cli", "chat"]


def _wait_for_text(
    buffer: List[str],
    needle: str,
    timeout: float,
    cursor: int = 0,
    lock: Optional[threading.Lock] = None,
) -> Tuple[bool, int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if lock:
            with lock:
                chunk = "".join(buffer)
        else:
            chunk = "".join(buffer)
        idx = chunk.find(needle, cursor)
        if idx != -1:
            return True, idx + len(needle)
        time.sleep(0.05)
    return False, cursor


def _wait_for_prompt(
    buffer: List[str],
    prompt_regex: re.Pattern,
    timeout: float,
    cursor: int = 0,
    lock: Optional[threading.Lock] = None,
) -> Tuple[bool, int]:
    if timeout <= 0:
        deadline = None
    else:
        deadline = time.time() + timeout
    while deadline is None or time.time() < deadline:
        if lock:
            with lock:
                chunk = "".join(buffer)
        else:
            chunk = "".join(buffer)
        match = prompt_regex.search(chunk, cursor)
        if match:
            return True, match.end()
        time.sleep(0.05)
    return False, cursor


def _wait_for_event(
    events: List[Dict[str, Any]],
    event_types: List[str],
    timeout: float,
    cursor: int = 0,
    lock: Optional[threading.Lock] = None,
) -> Tuple[bool, int]:
    deadline = time.time() + timeout
    target = {str(t).lower() for t in event_types}
    while time.time() < deadline:
        if lock:
            with lock:
                snapshot = events[cursor:]
        else:
            snapshot = events[cursor:]
        for idx, payload in enumerate(snapshot, start=cursor):
            if str(payload.get("type", "")).lower() in target:
                return True, idx + 1
        time.sleep(0.05)
    return False, cursor


def _strip_ansi(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _append_log(log_path: Optional[Path], payload: Dict[str, Any]) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scripted UAT sessions against the CLI.")
    parser.add_argument("--scenario", type=Path, help="JSON scenario file (steps, env overrides).")
    parser.add_argument("--entry", help="Override CLI entry (default: python -m researcher.cli chat).")
    parser.add_argument("--transcript", help="Transcript output path (passed to CLI).")
    parser.add_argument("--timeout", type=float, default=8.0, help="Default wait timeout per step.")
    parser.add_argument("--prompt-timeout", type=float, default=30.0, help="Wait for prompt timeout (0 = wait forever).")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay between inputs (seconds).")
    parser.add_argument("--echo", action="store_true", help="Echo CLI output during run.")
    parser.add_argument("--keep-open", action="store_true", help="Do not auto-quit after steps.")
    parser.add_argument("--no-auto-wait", action="store_true", help="Disable waiting for CLI prompts before sending input.")
    parser.add_argument("--use-socket", action="store_true", help="Send inputs via test socket instead of stdin.")
    parser.add_argument("--socket-host", default="127.0.0.1", help="Test socket host.")
    parser.add_argument("--socket-port", type=int, default=7002, help="Test socket port.")
    parser.add_argument("--socket-token", help="Token to authenticate socket inputs.")
    parser.add_argument("--socket-timeout", type=float, default=5.0, help="Seconds to wait for socket connection.")
    parser.add_argument("--mailbox", action="store_true", help="Mailbox mode: fire inputs and log events asynchronously.")
    parser.add_argument("--mailbox-log", type=Path, help="Mailbox log path (NDJSON).")
    parser.add_argument("--mailbox-duration", type=float, default=6.0, help="Seconds to keep session alive in mailbox mode.")
    parser.add_argument("--event-log", type=Path, help="Event log path (NDJSON) for any run.")
    parser.add_argument("--screenshot-dir", type=Path, help="Capture output snapshots (TXT) after each step.")
    parser.add_argument("--snapshot-lines", type=int, default=40, help="Lines to keep per snapshot (tail).")
    args = parser.parse_args()

    scenario = _load_scenario(args.scenario)
    env_overrides = scenario.get("env", {}) if isinstance(scenario, dict) else {}
    entry_override = scenario.get("entry") if isinstance(scenario, dict) else None
    steps = scenario.get("steps", []) if isinstance(scenario, dict) else []
    transcript = args.transcript or (scenario.get("transcript") if isinstance(scenario, dict) else None)
    use_socket = bool(scenario.get("use_socket", False)) if isinstance(scenario, dict) else False
    if args.use_socket:
        use_socket = True
    mailbox_mode = bool(scenario.get("mailbox", False)) if isinstance(scenario, dict) else False
    if args.mailbox:
        mailbox_mode = True
    socket_token = args.socket_token or (scenario.get("socket_token") if isinstance(scenario, dict) else None)
    mailbox_log = args.mailbox_log or (Path(scenario.get("mailbox_log")) if isinstance(scenario, dict) and scenario.get("mailbox_log") else None)
    if mailbox_mode and mailbox_log is None:
        mailbox_log = Path("logs") / "uat_mailbox.ndjson"
    mailbox_duration = float(args.mailbox_duration)
    if isinstance(scenario, dict) and "mailbox_duration" in scenario and "--mailbox-duration" not in sys.argv:
        mailbox_duration = float(scenario.get("mailbox_duration") or mailbox_duration)
    event_log = args.event_log or (Path(scenario.get("event_log")) if isinstance(scenario, dict) and scenario.get("event_log") else None)
    screenshot_dir = args.screenshot_dir or (Path(scenario.get("screenshot_dir")) if isinstance(scenario, dict) and scenario.get("screenshot_dir") else None)
    snapshot_lines = int(args.snapshot_lines or (scenario.get("snapshot_lines") if isinstance(scenario, dict) else 40))
    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
    auto_wait = scenario.get("auto_wait", True) if isinstance(scenario, dict) else True
    if args.no_auto_wait:
        auto_wait = False
    prompt_tokens = scenario.get("prompts") if isinstance(scenario, dict) else None
    if not prompt_tokens:
        prompt_tokens = [
            r"PROMPT_READY",
            r"\bYou:\s*$",
            r"martin: Handle for logbook\?",
            r"martin: Clock-in note",
            r"martin: Clock-out note",
            r"Approve running",
            r"Send to cloud\?",
            r"High-risk .* Type YES",
            r"Sandbox blocked",
            r"Edit command",
            r"Apply suggested fix commands",
            r"Mark onboarding complete\?",
        ]
    prompt_regex = re.compile("|".join(prompt_tokens), re.IGNORECASE | re.MULTILINE)
    expects_loop_ready = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        wait_for_event = step.get("wait_for_event")
        if isinstance(wait_for_event, str) and wait_for_event == "loop_ready":
            expects_loop_ready = True
            break
        if isinstance(wait_for_event, list) and "loop_ready" in wait_for_event:
            expects_loop_ready = True
            break

    cmd = _build_command(args.entry or entry_override)
    if transcript:
        cmd.extend(["--transcript", transcript])

    env = os.environ.copy()
    for key, value in (env_overrides or {}).items():
        env[str(key)] = str(value)
    if use_socket:
        env["MARTIN_TEST_SOCKET"] = "1"
        if socket_token:
            env["MARTIN_TEST_SOCKET_TOKEN"] = socket_token

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )

    output_buffer: List[str] = []
    output_lock = threading.Lock()
    event_buffer: List[Dict[str, Any]] = []
    event_lock = threading.Lock()
    socket_output_seen = threading.Event()
    pending_inputs: List[Dict[str, Any]] = []
    socket_conn = None
    socket_reader = None
    prompt_event = threading.Event()
    input_used_event = threading.Event()
    loop_ready_event = threading.Event()
    pong_event = threading.Event()
    last_line = {"value": ""}
    saw_prompt = {"value": False}

    def _signal_prompt_if_match(text: str) -> None:
        try:
            if text and prompt_regex.search(text):
                prompt_event.set()
        except Exception:
            pass

    capture_stdout = not use_socket

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            cleaned = _strip_ansi(line)
            allow_stdout = capture_stdout or (use_socket and not socket_output_seen.is_set())
            if allow_stdout:
                with output_lock:
                    if cleaned and cleaned == last_line["value"]:
                        continue
                    if cleaned:
                        last_line["value"] = cleaned
                    output_buffer.append(cleaned)
                _signal_prompt_if_match(cleaned)
                _append_log(mailbox_log, {"ts": time.time(), "type": "stdout", "text": cleaned})
                _append_log(event_log, {"ts": time.time(), "type": "stdout", "text": cleaned})
            if args.echo and (not use_socket or os.environ.get("MARTIN_TEST_SOCKET_DEBUG") == "1"):
                print(line, end="")

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    def _socket_reader(sock: socket.socket) -> None:
        buffer = ""
        while True:
            try:
                chunk = sock.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                msg_type = payload.get("type")
                text = payload.get("text")
                if os.environ.get("MARTIN_TEST_SOCKET_DEBUG") == "1":
                    print(f"[event] {msg_type}")
                logged_event = False
                if isinstance(text, str):
                    cleaned = _strip_ansi(text)
                    with output_lock:
                        if cleaned and cleaned == last_line["value"]:
                            pass
                        else:
                            if cleaned:
                                last_line["value"] = cleaned
                            output_buffer.append(cleaned)
                    if args.echo:
                        print(text, end="")
                    _append_log(mailbox_log, {"ts": time.time(), "type": msg_type or "socket", "text": cleaned})
                    _append_log(event_log, {"ts": time.time(), "type": msg_type or "socket", "text": cleaned})
                    logged_event = True
                if msg_type:
                    with event_lock:
                        event_buffer.append({"ts": time.time(), "type": msg_type, "text": text})
                    if not logged_event:
                        _append_log(event_log, {"ts": time.time(), "type": msg_type, "text": text})
                if msg_type == "output":
                    socket_output_seen.set()
                if msg_type == "prompt":
                    with output_lock:
                        output_buffer.append("PROMPT_READY")
                    prompt_event.set()
                    saw_prompt["value"] = True
                if msg_type == "input_used":
                    input_used_event.set()
                if msg_type == "loop_ready":
                    loop_ready_event.set()
                if msg_type == "pong":
                    pong_event.set()

    def _send(text: str) -> None:
        if proc.stdin is None:
            return
        if socket_conn is not None:
            payload = json.dumps({"type": "input", "text": text, "token": socket_token}, ensure_ascii=False) + "\n"
            try:
                socket_conn.sendall(payload.encode("utf-8"))
                if args.echo:
                    print(f"[sent] {text}")
            except Exception:
                try:
                    with socket.create_connection((args.socket_host, args.socket_port), timeout=1.0) as send_sock:
                        send_sock.sendall(payload.encode("utf-8"))
                except Exception:
                    pass
            return
        proc.stdin.write(text + "\n")
        proc.stdin.flush()

    def _conditions_met(wait_text: Any, wait_event: Any) -> bool:
        if wait_text:
            tokens = [wait_text] if isinstance(wait_text, str) else list(wait_text)
            with output_lock:
                blob = "".join(output_buffer)
            for token in tokens:
                if token and token not in blob:
                    return False
        if wait_event:
            tokens = [wait_event] if isinstance(wait_event, str) else list(wait_event)
            with event_lock:
                seen = {payload.get("type") for payload in event_buffer}
            for token in tokens:
                if token not in seen:
                    return False
        return True

    def _flush_pending() -> None:
        if not pending_inputs:
            return
        remaining: List[Dict[str, Any]] = []
        for item in pending_inputs:
            if _conditions_met(item.get("input_when_text"), item.get("input_when_event")):
                _send(item["input"])
            else:
                remaining.append(item)
        pending_inputs[:] = remaining

    if use_socket:
        deadline = time.time() + float(args.socket_timeout)
        while time.time() < deadline:
            try:
                socket_conn = socket.create_connection((args.socket_host, args.socket_port), timeout=1.0)
                try:
                    socket_conn.settimeout(None)
                except Exception:
                    pass
                break
            except Exception:
                time.sleep(0.2)
        if not socket_conn:
            print("[error] Could not connect to test socket.", file=sys.stderr)
            proc.terminate()
            return 2
        socket_reader = threading.Thread(target=_socket_reader, args=(socket_conn,), daemon=True)
        socket_reader.start()
        try:
            socket_conn.sendall((json.dumps({"type": "ping"}, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception:
            pass
        if not pong_event.wait(timeout=float(args.socket_timeout)):
            print("[error] Test socket did not respond to ping.", file=sys.stderr)
            proc.terminate()
            return 2
        if not loop_ready_event.wait(timeout=float(args.socket_timeout)) and not expects_loop_ready:
            print("[warn] Loop readiness not confirmed before steps.", file=sys.stderr)

    cursor = 0
    event_cursor = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        text = step.get("input")
        wait_for = step.get("wait_for")
        wait_for_event = step.get("wait_for_event")
        input_when_text = step.get("input_when_text")
        input_when_event = step.get("input_when_event")
        if isinstance(text, str):
            should_send = True
            if input_when_text:
                if not _conditions_met(input_when_text, None):
                    should_send = False
            if should_send and input_when_event:
                if not _conditions_met(None, input_when_event):
                    should_send = False
            if not should_send:
                pending_inputs.append(
                    {
                        "input": text,
                        "input_when_text": input_when_text,
                        "input_when_event": input_when_event,
                    }
                )
                continue
            if auto_wait and not wait_for and not mailbox_mode:
                if use_socket:
                    found = True
                else:
                    step_timeout = float(step.get("prompt_timeout", args.prompt_timeout))
                    found, cursor = _wait_for_prompt(
                        output_buffer, prompt_regex, step_timeout, cursor, output_lock
                    )
                if not found:
                    print("[warn] Prompt not detected before input.", file=sys.stderr)
            _send(text)
            if use_socket and not mailbox_mode:
                used_timeout = float(step.get("input_timeout", args.timeout))
                if used_timeout <= 0:
                    used_timeout = 3600.0
                found, event_cursor = _wait_for_event(
                    event_buffer, ["input_used"], used_timeout, event_cursor, event_lock
                )
                if not found:
                    matched = False
                    with event_lock:
                        for payload in event_buffer:
                            if payload.get("type") in ("input_used", "input_ack") and payload.get("text") == text:
                                matched = True
                                break
                    if not matched:
                        print("[warn] Input not consumed before timeout.", file=sys.stderr)
                        break
        if isinstance(wait_for, str):
            if mailbox_mode:
                continue
            timeout = float(step.get("timeout", args.timeout))
            found, cursor = _wait_for_text(output_buffer, wait_for, timeout, cursor, output_lock)
            if not found:
                print(f"[warn] Expected text not found: {wait_for!r}", file=sys.stderr)
        if wait_for_event:
            if mailbox_mode:
                continue
            if isinstance(wait_for_event, str):
                event_types = [wait_for_event]
            elif isinstance(wait_for_event, list):
                event_types = [str(item) for item in wait_for_event if item]
            else:
                event_types = []
            if event_types:
                timeout = float(step.get("timeout", args.timeout))
                found, event_cursor = _wait_for_event(event_buffer, event_types, timeout, event_cursor, event_lock)
                if not found:
                    print(f"[warn] Expected event not found: {event_types!r}", file=sys.stderr)
        if mailbox_mode:
            continue
        sleep_for = step.get("sleep", args.delay)
        if sleep_for:
            time.sleep(float(sleep_for))
        _flush_pending()
        if screenshot_dir is not None:
            with output_lock:
                snapshot = "".join(output_buffer)
            lines = snapshot.splitlines()
            tail = "\n".join(lines[-snapshot_lines:]) if snapshot_lines > 0 else snapshot
            filename = f"step_{event_cursor:03d}.txt"
            try:
                (screenshot_dir / filename).write_text(tail, encoding="utf-8")
            except Exception:
                pass

    if mailbox_mode:
        deadline = time.time() + mailbox_duration
        while time.time() < deadline:
            _flush_pending()
            time.sleep(0.1)
        _send("quit")
        time.sleep(0.25)
    if not args.keep_open and not mailbox_mode:
        _send("quit")
        time.sleep(0.25)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
    if socket_conn:
        try:
            socket_conn.close()
        except Exception:
            pass

    return proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
