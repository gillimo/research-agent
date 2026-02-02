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
from typing import Any, Callable, Dict, List, Optional, Tuple


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
    on_tick: Optional[Callable[[], None]] = None,
) -> Tuple[bool, int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if on_tick:
            on_tick()
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
    on_tick: Optional[Callable[[], None]] = None,
) -> Tuple[bool, int]:
    if timeout <= 0:
        deadline = None
    else:
        deadline = time.time() + timeout
    while deadline is None or time.time() < deadline:
        if on_tick:
            on_tick()
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
    on_tick: Optional[Callable[[], None]] = None,
) -> Tuple[bool, int]:
    deadline = time.time() + timeout
    target = {str(t).lower() for t in event_types}
    while time.time() < deadline:
        if on_tick:
            on_tick()
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


def _wait_for_prompt_text(
    events: List[Dict[str, Any]],
    tokens: List[str],
    timeout: float,
    cursor: int = 0,
    lock: Optional[threading.Lock] = None,
    on_tick: Optional[Callable[[], None]] = None,
) -> Tuple[bool, int]:
    deadline = time.time() + timeout
    normalized = [token for token in tokens if token]
    while time.time() < deadline:
        if on_tick:
            on_tick()
        if lock:
            with lock:
                snapshot = events[cursor:]
        else:
            snapshot = events[cursor:]
        for idx, payload in enumerate(snapshot, start=cursor):
            if payload.get("type") != "prompt":
                continue
            text = _strip_ansi(payload.get("text") or "")
            if any(token in text for token in normalized):
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
    parser.add_argument("--mailbox-session", action="store_true", help="Keep mailbox session open for extended async testing.")
    parser.add_argument("--mailbox-log", type=Path, help="Mailbox log path (NDJSON).")
    parser.add_argument("--mailbox-duration", type=float, default=6.0, help="Seconds to keep session alive in mailbox mode.")
    parser.add_argument("--mailbox-grace", type=float, default=0.6, help="Seconds to keep collecting output after last input.")
    parser.add_argument("--mailbox-collect-path", type=Path, help="Write mailbox collects to a text file.")
    parser.add_argument("--mailbox-idle-collect", type=float, default=0.0, help="Seconds between idle mailbox collects (0 = off).")
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
    mailbox_session = bool(scenario.get("mailbox_session", False)) if isinstance(scenario, dict) else False
    if args.mailbox_session:
        mailbox_session = True
    if mailbox_session:
        mailbox_mode = True
    socket_token = args.socket_token or (scenario.get("socket_token") if isinstance(scenario, dict) else None)
    mailbox_log = args.mailbox_log or (Path(scenario.get("mailbox_log")) if isinstance(scenario, dict) and scenario.get("mailbox_log") else None)
    if mailbox_mode and mailbox_log is None:
        mailbox_log = Path("logs") / "uat_mailbox.ndjson"
    mailbox_duration = float(args.mailbox_duration)
    if isinstance(scenario, dict) and "mailbox_duration" in scenario and "--mailbox-duration" not in sys.argv:
        mailbox_duration = float(scenario.get("mailbox_duration") or mailbox_duration)
    if mailbox_session and "mailbox_duration" not in (scenario or {}) and "--mailbox-duration" not in sys.argv:
        mailbox_duration = 0.0
    mailbox_grace_s = float(args.mailbox_grace)
    if isinstance(scenario, dict) and "mailbox_grace_s" in scenario and "--mailbox-grace" not in sys.argv:
        mailbox_grace_s = float(scenario.get("mailbox_grace_s") or mailbox_grace_s)
    mailbox_collect_path = args.mailbox_collect_path or (
        Path(scenario.get("mailbox_collect_path")) if isinstance(scenario, dict) and scenario.get("mailbox_collect_path") else None
    )
    mailbox_idle_s = float(args.mailbox_idle_collect)
    if isinstance(scenario, dict) and "mailbox_idle_s" in scenario and "--mailbox-idle-collect" not in sys.argv:
        mailbox_idle_s = float(scenario.get("mailbox_idle_s") or mailbox_idle_s)
    mailbox_start_on_prompt = bool(scenario.get("mailbox_start_on_prompt", False)) if isinstance(scenario, dict) else False
    mailbox_start_prompt = scenario.get("mailbox_start_prompt") if isinstance(scenario, dict) else None
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
    session_id = str(uuid.uuid4())
    collect_index = 0
    mailbox_collect_requested = False
    mailbox_collect_expect: List[str] = []
    pending_inputs: List[Dict[str, Any]] = []
    consumed_text_counts: Dict[str, int] = {}
    consumed_event_counts: Dict[str, int] = {}
    consumed_prompt_counts: Dict[str, int] = {}
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

    mailbox_start = {"ts": None}

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
                    if mailbox_mode and mailbox_start_on_prompt and mailbox_start["ts"] is None:
                        if mailbox_start_prompt:
                            if isinstance(text, str) and mailbox_start_prompt in text:
                                mailbox_start["ts"] = time.time()
                        else:
                            mailbox_start["ts"] = time.time()
                    if mailbox_mode:
                        _flush_pending()
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

    def _collect_output() -> str:
        nonlocal collect_index
        with output_lock:
            if collect_index >= len(output_buffer):
                return ""
            chunk = "".join(output_buffer[collect_index:])
            collect_index = len(output_buffer)
            return chunk

    def _write_collect(text: str, final: bool = False) -> None:
        if not mailbox_collect_path or not text:
            return
        try:
            mailbox_collect_path.parent.mkdir(parents=True, exist_ok=True)
            header = f"\n--- mailbox_collect ts={time.time():.3f} session={session_id} final={final} ---\n"
            with mailbox_collect_path.open("a", encoding="utf-8") as handle:
                handle.write(header)
                handle.write(text)
                if not text.endswith("\n"):
                    handle.write("\n")
        except Exception:
            pass

    if mailbox_session:
        _append_log(
            event_log,
            {
                "ts": time.time(),
                "type": "mailbox_session_start",
                "session_id": session_id,
                "pid": proc.pid,
            },
        )

    def _count_text_matches(token: str) -> int:
        if not token:
            return 0
        prompt_texts: List[str] = []
        with event_lock:
            for payload in event_buffer:
                if payload.get("type") == "prompt" and isinstance(payload.get("text"), str):
                    prompt_texts.append(payload.get("text") or "")
        if prompt_texts:
            prompt_count = sum(text.count(token) for text in prompt_texts if token in text)
            if prompt_count > 0:
                return prompt_count
        with output_lock:
            blob = "".join(output_buffer)
        return blob.count(token)

    def _count_prompt_matches(token: str) -> int:
        if not token:
            return 0
        prompt_texts: List[str] = []
        with event_lock:
            for payload in event_buffer:
                if payload.get("type") == "prompt" and isinstance(payload.get("text"), str):
                    prompt_texts.append(_strip_ansi(payload.get("text") or ""))
        return sum(text.count(token) for text in prompt_texts if token in text)

    def _count_event_matches(token: str) -> int:
        if not token:
            return 0
        with event_lock:
            return sum(1 for payload in event_buffer if payload.get("type") == token)

    def _baseline_counts(tokens: List[str], counter) -> Dict[str, int]:
        baseline: Dict[str, int] = {}
        for token in tokens:
            if token:
                baseline[token] = counter(token)
        return baseline

    def _latest_prompt_text() -> str:
        with event_lock:
            for payload in reversed(event_buffer):
                if payload.get("type") == "prompt" and isinstance(payload.get("text"), str):
                    return payload.get("text") or ""
        return ""

    def _event_seen(token: str) -> bool:
        if not token:
            return False
        with event_lock:
            return any(payload.get("type") == token for payload in event_buffer)

    def _conditions_met(
        wait_text: Any,
        wait_event: Any,
        baseline_text: Optional[Dict[str, int]] = None,
        baseline_event: Optional[Dict[str, int]] = None,
    ) -> bool:
        if wait_text:
            tokens = [wait_text] if isinstance(wait_text, str) else list(wait_text)
            for token in tokens:
                if not token:
                    continue
                total = _count_text_matches(token)
                baseline = 0 if not baseline_text else baseline_text.get(token, 0)
                consumed = consumed_text_counts.get(token, 0)
                if total <= max(baseline, consumed):
                    return False
        if wait_event:
            tokens = [wait_event] if isinstance(wait_event, str) else list(wait_event)
            for token in tokens:
                if not token:
                    continue
                total = _count_event_matches(token)
                baseline = 0 if not baseline_event else baseline_event.get(token, 0)
                consumed = consumed_event_counts.get(token, 0)
                if total <= max(baseline, consumed):
                    return False
        return True

    def _conditions_met_prompt(
        wait_prompt: Any,
        baseline_prompt: Optional[Dict[str, int]] = None,
    ) -> bool:
        if wait_prompt:
            tokens = [wait_prompt] if isinstance(wait_prompt, str) else list(wait_prompt)
            for token in tokens:
                if not token:
                    continue
                total = _count_prompt_matches(token)
                baseline = 0 if not baseline_prompt else baseline_prompt.get(token, 0)
                consumed = consumed_prompt_counts.get(token, 0)
                if total <= max(baseline, consumed):
                    return False
        return True

    def _flush_pending() -> None:
        if not pending_inputs:
            return
        latest_prompt = _latest_prompt_text()
        remaining: List[Dict[str, Any]] = []
        for item in pending_inputs:
            bypass_prompt_gate = False
            if mailbox_mode and item.get("input_when_prompt"):
                tokens = [item.get("input_when_prompt")] if isinstance(item.get("input_when_prompt"), str) else list(item.get("input_when_prompt") or [])
                if any(token and token in latest_prompt for token in tokens):
                    bypass_prompt_gate = True
                else:
                    remaining.append(item)
                    continue
            if _conditions_met(
                item.get("input_when_text"),
                item.get("input_when_event"),
                item.get("baseline_text"),
                item.get("baseline_event"),
            ) and (bypass_prompt_gate or _conditions_met_prompt(
                item.get("input_when_prompt"),
                item.get("baseline_prompt"),
            )):
                _send(item["input"])
                _append_log(
                    event_log,
                    {
                        "ts": time.time(),
                        "type": "pending_send",
                        "input": item.get("input"),
                        "input_when_text": item.get("input_when_text"),
                        "input_when_event": item.get("input_when_event"),
                        "input_when_prompt": item.get("input_when_prompt"),
                    },
                )
                for token in item.get("baseline_text", {}):
                    consumed_text_counts[token] = consumed_text_counts.get(token, 0) + 1
                for token in item.get("baseline_event", {}):
                    consumed_event_counts[token] = consumed_event_counts.get(token, 0) + 1
                for token in item.get("baseline_prompt", {}):
                    consumed_prompt_counts[token] = consumed_prompt_counts.get(token, 0) + 1
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
    prompt_cursor = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        text = step.get("input")
        wait_for = step.get("wait_for")
        wait_for_event = step.get("wait_for_event")
        wait_for_prompt = step.get("wait_for_prompt")
        input_when_text = step.get("input_when_text")
        input_when_event = step.get("input_when_event")
        input_when_prompt = step.get("input_when_prompt")
        queue_input = bool(step.get("queue_input", False))
        collect = bool(step.get("collect", False))
        replay_path = step.get("replay_path")
        replay_prefix = step.get("replay_prefix", "")
        expect = step.get("expect") if isinstance(step.get("expect"), list) else []
        if isinstance(step.get("expect"), str):
            expect = [step.get("expect")]
        if replay_path:
            try:
                content = Path(replay_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                content = []
            for line in content:
                line = line.strip()
                if not line:
                    continue
                pending_inputs.append(
                    {
                        "input": f"{replay_prefix}{line}",
                        "input_when_text": None,
                        "input_when_event": None,
                        "input_when_prompt": "You:",
                        "baseline_text": _baseline_counts([], _count_text_matches),
                        "baseline_event": _baseline_counts([], _count_event_matches),
                        "baseline_prompt": _baseline_counts(["You:"], _count_prompt_matches),
                    }
                )
            continue
        if isinstance(text, str):
            should_send = True
            if mailbox_mode and (input_when_text or input_when_event):
                should_send = False
            if mailbox_mode and input_when_prompt and not (input_when_text or input_when_event):
                prompt_tokens = [input_when_prompt] if isinstance(input_when_prompt, str) else list(input_when_prompt or [])
                latest_prompt = _latest_prompt_text()
                if not any(token and token in latest_prompt for token in prompt_tokens):
                    should_send = False
            if input_when_prompt:
                tokens = [input_when_prompt] if isinstance(input_when_prompt, str) else list(input_when_prompt or [])
                if not mailbox_mode:
                    prompt_text = _latest_prompt_text()
                    if not any(token and token in prompt_text for token in tokens):
                        should_send = False
                else:
                    baseline_prompt = _baseline_counts(tokens, _count_prompt_matches)
                    if not _conditions_met_prompt(input_when_prompt, baseline_prompt):
                        should_send = False
            if queue_input and mailbox_mode:
                if not input_when_prompt:
                    input_when_prompt = "You:"
                should_send = False
            if input_when_text:
                tokens = [input_when_text] if isinstance(input_when_text, str) else list(input_when_text or [])
                if not mailbox_mode:
                    prompt_text = _latest_prompt_text()
                    if not any(token and token in prompt_text for token in tokens):
                        with output_lock:
                            blob = "".join(output_buffer)
                        if not any(token and token in blob for token in tokens):
                            should_send = False
                else:
                    baseline_text = _baseline_counts(tokens, _count_text_matches)
                    if not _conditions_met(input_when_text, None, baseline_text, None):
                        should_send = False
            if should_send and input_when_event:
                tokens = [input_when_event] if isinstance(input_when_event, str) else list(input_when_event or [])
                if not mailbox_mode:
                    if not any(_event_seen(token) for token in tokens):
                        should_send = False
                else:
                    baseline_event = _baseline_counts(tokens, _count_event_matches)
                    if not _conditions_met(None, input_when_event, None, baseline_event):
                        should_send = False
            if not should_send:
                text_tokens = [input_when_text] if isinstance(input_when_text, str) else list(input_when_text or [])
                event_tokens = [input_when_event] if isinstance(input_when_event, str) else list(input_when_event or [])
                prompt_tokens = [input_when_prompt] if isinstance(input_when_prompt, str) else list(input_when_prompt or [])
                pending_inputs.append(
                    {
                        "input": text,
                        "input_when_text": input_when_text,
                        "input_when_event": input_when_event,
                        "input_when_prompt": input_when_prompt,
                        "baseline_text": _baseline_counts(text_tokens, _count_text_matches),
                        "baseline_event": _baseline_counts(event_tokens, _count_event_matches),
                        "baseline_prompt": _baseline_counts(prompt_tokens, _count_prompt_matches),
                    }
                )
                continue
            if input_when_text:
                for token in [input_when_text] if isinstance(input_when_text, str) else list(input_when_text or []):
                    if token:
                        consumed_text_counts[token] = consumed_text_counts.get(token, 0) + 1
            if input_when_event:
                for token in [input_when_event] if isinstance(input_when_event, str) else list(input_when_event or []):
                    if token:
                        consumed_event_counts[token] = consumed_event_counts.get(token, 0) + 1
            if input_when_prompt:
                for token in [input_when_prompt] if isinstance(input_when_prompt, str) else list(input_when_prompt or []):
                    if token:
                        consumed_prompt_counts[token] = consumed_prompt_counts.get(token, 0) + 1
            if auto_wait and not wait_for and not mailbox_mode:
                if use_socket:
                    found = True
                else:
                    step_timeout = float(step.get("prompt_timeout", args.prompt_timeout))
                    found, cursor = _wait_for_prompt(
                        output_buffer,
                        prompt_regex,
                        step_timeout,
                        cursor,
                        output_lock,
                        on_tick=_flush_pending,
                    )
                if not found:
                    print("[warn] Prompt not detected before input.", file=sys.stderr)
            _send(text)
            if use_socket and not mailbox_mode:
                used_timeout = float(step.get("input_timeout", args.timeout))
                if used_timeout <= 0:
                    used_timeout = 3600.0
                found, event_cursor = _wait_for_event(
                    event_buffer,
                    ["input_used"],
                    used_timeout,
                    event_cursor,
                    event_lock,
                    on_tick=_flush_pending,
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
            timeout = float(step.get("timeout", args.timeout))
            found, cursor = _wait_for_text(
                output_buffer,
                wait_for,
                timeout,
                cursor,
                output_lock,
                on_tick=_flush_pending,
            )
            if not found:
                print(f"[warn] Expected text not found: {wait_for!r}", file=sys.stderr)
        if wait_for_event:
            if isinstance(wait_for_event, str):
                event_types = [wait_for_event]
            elif isinstance(wait_for_event, list):
                event_types = [str(item) for item in wait_for_event if item]
            else:
                event_types = []
            if event_types:
                timeout = float(step.get("timeout", args.timeout))
                found, event_cursor = _wait_for_event(
                    event_buffer,
                    event_types,
                    timeout,
                    event_cursor,
                    event_lock,
                    on_tick=_flush_pending,
                )
                if not found:
                    print(f"[warn] Expected event not found: {event_types!r}", file=sys.stderr)
        if wait_for_prompt:
            tokens = [wait_for_prompt] if isinstance(wait_for_prompt, str) else list(wait_for_prompt or [])
            if tokens:
                timeout = float(step.get("timeout", args.timeout))
                found, prompt_cursor = _wait_for_prompt_text(
                    event_buffer,
                    tokens,
                    timeout,
                    prompt_cursor,
                    event_lock,
                    on_tick=_flush_pending,
                )
                if not found:
                    print(f"[warn] Expected prompt not found: {tokens!r}", file=sys.stderr)
        if collect and mailbox_mode:
            mailbox_collect_requested = True
            mailbox_collect_expect.extend([token for token in expect if token])
            continue
        if collect:
            collected = _collect_output()
            if collected:
                _append_log(
                    event_log,
                    {
                        "ts": time.time(),
                        "type": "mailbox_collect",
                        "session_id": session_id,
                        "chars": len(collected),
                        "lines": len(collected.splitlines()),
                        "text": collected,
                    },
                )
                _write_collect(collected)
            if expect:
                missing = [token for token in expect if token and token not in (collected or "")]
                if missing:
                    print(f"[warn] Expected tokens missing in mailbox collect: {missing!r}", file=sys.stderr)
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
        if not mailbox_start_on_prompt:
            mailbox_start["ts"] = time.time()
        last_idle_collect = time.time()
        while True:
            if proc.poll() is not None:
                break
            if mailbox_start["ts"] is None:
                _flush_pending()
                time.sleep(0.1)
                continue
            if mailbox_duration > 0:
                deadline = mailbox_start["ts"] + mailbox_duration
                if time.time() >= deadline:
                    break
            if mailbox_idle_s > 0 and (time.time() - last_idle_collect) >= mailbox_idle_s:
                idle_collected = _collect_output()
                if idle_collected:
                    _append_log(
                        event_log,
                        {
                            "ts": time.time(),
                            "type": "mailbox_collect_idle",
                            "session_id": session_id,
                            "chars": len(idle_collected),
                            "lines": len(idle_collected.splitlines()),
                            "text": idle_collected,
                        },
                    )
                    _write_collect(idle_collected)
                last_idle_collect = time.time()
            _flush_pending()
            time.sleep(0.1)
        if mailbox_duration > 0 and mailbox_grace_s > 0:
            grace_deadline = time.time() + mailbox_grace_s
            while time.time() < grace_deadline:
                _flush_pending()
                time.sleep(0.1)
        final_collected = _collect_output()
        if final_collected:
            _append_log(
                event_log,
                {
                    "ts": time.time(),
                    "type": "mailbox_collect",
                    "session_id": session_id,
                    "chars": len(final_collected),
                    "lines": len(final_collected.splitlines()),
                    "text": final_collected,
                    "final": True,
                },
            )
            _write_collect(final_collected, final=True)
            if mailbox_collect_requested and mailbox_collect_expect:
                missing = [token for token in mailbox_collect_expect if token and token not in (final_collected or "")]
                if missing:
                    print(f"[warn] Expected tokens missing in mailbox collect: {missing!r}", file=sys.stderr)
        if pending_inputs:
            _append_log(
                event_log,
                {
                    "ts": time.time(),
                    "type": "pending_inputs",
                    "count": len(pending_inputs),
                    "pending": [
                        {
                            "input_when_text": item.get("input_when_text"),
                            "input_when_event": item.get("input_when_event"),
                            "input_when_prompt": item.get("input_when_prompt"),
                        }
                        for item in pending_inputs
                    ],
                },
            )
        if mailbox_session:
            _append_log(
                event_log,
                {
                    "ts": time.time(),
                    "type": "mailbox_session_end",
                    "session_id": session_id,
                    "pid": proc.pid,
                    "last_prompt": _strip_ansi(_latest_prompt_text()),
                },
            )
        if not mailbox_session:
            latest_prompt = _strip_ansi(_latest_prompt_text())
            if latest_prompt:
                exit_prompts = [
                    ("Approve running", "no"),
                    ("Apply suggested fix commands now", "no"),
                    ("Command touches outside workspace", "no"),
                    ("Mark onboarding complete", "no"),
                    ("Clock-in note", ".skip"),
                    ("Handle for logbook", "user"),
                ]
                for token, response in exit_prompts:
                    if token in latest_prompt:
                        if pending_inputs:
                            if not any((item.get("input_when_prompt") or "").find(token) >= 0 for item in pending_inputs):
                                continue
                        _send(response)
                        _append_log(
                            event_log,
                            {
                                "ts": time.time(),
                                "type": "mailbox_exit_prompt",
                                "prompt": latest_prompt,
                                "response": response,
                            },
                        )
                        time.sleep(0.1)
                        break
            _send("quit")
            time.sleep(0.25)
    if not args.keep_open and not mailbox_mode:
        _send("quit")
        time.sleep(0.25)

    if not mailbox_session:
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
