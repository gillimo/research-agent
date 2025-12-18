#!/usr/bin/env python
# Agent Martin OS - Single-File Release (sanitized)
# Version: v1.4.7 (waiter = no menu picking; internal abilities; confirmations)
# Date: 2025-10-30  America/New_York
# Python: 3.9+   Platform: Linux-first (Windows-aware)
#
# Highlights:
# - Env models: MODEL_MAIN (default gpt-4.1), MODEL_MINI (default gpt-4.1-mini)
# - Chef (mini): intent + question_summaries
# - Waiter (mini): GUIDANCE + BEHAVIOR + QUESTIONS + full capability inventory (NO picking/filtering)
# - Main: soft personality; follows Waiter guidance
# - Generic internal abilities: `command: martin.<ability_key> <payload>`
# - Pre-exec confirmation (initial + correction-flow), yes/no/abort logic
# - Rephraser suppressed on init (bug fix)
# - APT noninteractive is opt-in (MARTIN_APT_NONINTERACTIVE=1)
#
# NOTE: API key is NOT hardcoded; set OPENAI_API_KEY in your env/.env.

import os, json, time, hashlib, datetime, subprocess, shlex, re, shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
from subprocess import Popen, PIPE
import requests
from tqdm import tqdm

APP_NAME = "Agent Martin OS"
VERSION = "v1.4.7"
ROOT_DIR = Path.cwd()
STATE_FILE = ROOT_DIR / ".martin_state.json"
LOG_DIR = ROOT_DIR / "logs"
LEDGER_FILE = LOG_DIR / "martin_ledger.ndjson"

# Sanitized: API key must come from environment; do not hardcode
API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
if not API_KEY:
    print("\033[93mMartin: Warning - OPENAI_API_KEY not set; API calls will fail.\033[0m")
RESPONSES_URL = "https://api.openai.com/v1/responses"
TIMEOUT_S = 60
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {API_KEY or ''}",
}

# ---- Model selection (env-overridable) ----
MODEL_MAIN = os.getenv("MARTIN_MODEL_MAIN", "gpt-4.1")
MODEL_MINI = os.getenv("MARTIN_MODEL_MINI", "gpt-4.1-mini")

SHOW_TURN_BAR = True
SHOW_API_BARS = True
ECHO_INTERACTIVE = False
HEARTBEAT_SUMMARY_EVERY_S = 25
HEARTBEAT_MIN_CHARS = 600
MAX_RETRIES = 3
BACKOFF_BASE_S = 0.75
CMD_TIMEOUT_S = 300

interaction_history: List[str] = []
current_username = os.getenv("USER") or "pi"

# ===== T1: State & Ledger =====

def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"

def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

DEFAULT_STATE: Dict[str, Any] = {
    "current_version": VERSION,
    "session_count": 0,
    "last_session": {
        "started_at": None, "ended_at": None,
        "num_commands": 0, "last_exit_code": None,
        "summary": None,
    },
    "platform": {
        "system": os.uname().sysname if hasattr(os, "uname") else "Unknown",
        "release": os.uname().release if hasattr(os, "uname") else "Unknown",
        "python": ".".join(map(str, (os.sys.version_info.major, os.sys.version_info.minor, os.sys.version_info.micro))),
    },
    "ledger": {"entries": 0, "last_hash": None},
    "workspace": {"path": "./workspace", "last_file": ""}
}

def _read_json(path: Path, default: Any) -> Any:
    if not path.exists(): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception:
        return default

def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def load_state() -> Dict[str, Any]:
    st = _read_json(STATE_FILE, DEFAULT_STATE.copy())
    for k, v in DEFAULT_STATE.items():
        if k not in st: st[k] = v
    if "ledger" not in st: st["ledger"] = {"entries": 0, "last_hash": None}
    return st

def save_state(st: Dict[str, Any]) -> None:
    _write_json(STATE_FILE, st)

def _ledger_entry(event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ts": _now_iso(), "version": VERSION, "event": event, "data": data}

def append_ledger(st: Dict[str, Any], entry: Dict[str, Any]) -> None:
    _ensure_dirs()
    prev_hash = st["ledger"].get("last_hash")
    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    new_hash = _sha256_bytes(((prev_hash or "") + payload).encode("utf-8"))
    line = json.dumps({"entry": entry, "prev_hash": prev_hash, "hash": new_hash}, ensure_ascii=False)
    with open(LEDGER_FILE, "a", encoding="utf-8") as f: f.write(line + "\n")
    st["ledger"]["entries"] = int(st["ledger"].get("entries", 0)) + 1
    st["ledger"]["last_hash"] = new_hash
    save_state(st)

def log_event(st: Dict[str, Any], event: str, **data: Any) -> None:
    append_ledger(st, _ledger_entry(event, data))

class SessionCtx:
    def __init__(self, st: Dict[str, Any]) -> None:
        self.st = st; self.started_at = _now_iso(); self.commands = 0; self.last_rc: Optional[int] = None
    def begin(self) -> None:
        self.st["session_count"] = int(self.st.get("session_count", 0)) + 1
        self.st["last_session"] = {"started_at": self.started_at, "ended_at": None,
                                   "num_commands": 0, "last_exit_code": None, "summary": None}
        save_state(self.st); log_event(self.st, "session_start", started_at=self.started_at, version=VERSION)
    def record_cmd(self, rc: int) -> None:
        self.commands += 1; self.last_rc = rc
    def end(self) -> None:
        ended_at = _now_iso()
        summary = {"total_commands": self.commands, "last_exit_code": self.last_rc}
        self.st["last_session"] = {"started_at": self.started_at, "ended_at": ended_at,
                                   "num_commands": self.commands, "last_exit_code": self.last_rc, "summary": summary}
        save_state(self.st); log_event(self.st, "session_end", ended_at=ended_at, **summary)

# ===== OpenAI Responses API helpers =====

def _post_responses(payload, timeout=TIMEOUT_S, label="API"):
    last_err = None
    bar_ctx = tqdm(total=MAX_RETRIES, desc=label, unit="try", leave=False) if SHOW_API_BARS else None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(RESPONSES_URL, headers=HEADERS, json=payload, timeout=timeout)
            status = r.status_code; text = r.text or ""
            if status == 200:
                try:
                    data = r.json()
                except Exception as e:
                    last_err = {"message": "Invalid JSON from API", "detail": str(e), "body": text[:2000]}
                    if bar_ctx: bar_ctx.update(1)
                    break
                if bar_ctx:
                    bar_ctx.update(MAX_RETRIES - bar_ctx.n); bar_ctx.close()
                return data
            else:
                try:
                    j = r.json()
                except Exception:
                    j = {}
                api_err = j.get("error")
                if isinstance(api_err, dict):
                    last_err = {"message": api_err.get("message") or f"HTTP {status}",
                                "type": api_err.get("type"), "param": api_err.get("param"),
                                "code": api_err.get("code"), "http_status": status}
                else:
                    last_err = {"message": f"HTTP {status}", "http_status": status, "body": text[:2000]}
        except requests.RequestException as e:
            last_err = {"message": "Network error", "detail": str(e)}
        if bar_ctx:
            bar_ctx.update(1)
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))
    if bar_ctx:
        bar_ctx.close()
    return {"error": last_err or {"message": "Unknown error"}}

def _extract_output_text(resp_json: dict) -> str:
    if not isinstance(resp_json, dict):
        return ""
    err = resp_json.get("error", None)
    if err is not None:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        print(f"\033[93mMartin: OpenAI error: {msg}\033[0m")
        return ""
    ot = resp_json.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot.strip()
    out = []
    try:
        for item in resp_json.get("output", []):
            if isinstance(item, dict) and item.get("type") == "message":
                for c in item.get("content", []):
                    if isinstance(c, dict):
                        if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                            out.append(c["text"])
                        elif "text" in c and isinstance(c["text"], str):
                            out.append(c["text"])
    except Exception:
        return ""
    return "\n".join([s for s in out if s]).strip()

# ===== Summarizers / Diagnosis / Rephraser =====

def summarize_progress(text: str) -> str:
    prompt = (
        "You are Martin. Summarize the recent CLI output in 2-3 short bullet points. "
        "Be concrete (packages, compiling, errors). No fluff.\n\nRecent output:\n" + text[-2000:]
    )
    payload = {
        "model": MODEL_MINI,
        "input": [
            {"role": "system", "content": "Be concise and informative."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_output_tokens": 120,
    }
    resp = _post_responses(payload, label="Summary")
    return _extract_output_text(resp) or ""

def diagnose_failure(cmd: str, output: str) -> str:
    prompt = (
        "You are Martin. Analyze the failed command and output. Provide a brief diagnosis (1-3 sentences) "
        "then propose the safest fix steps. If commands are needed, list them as lines starting with 'command: ' "
        "and ensure they are non-interactive. No code blocks.\n\n"
        f"Command:\n{cmd}\n\nOutput (tail):\n{output[-4000:]}"
    )
    payload = {
        "model": MODEL_MAIN,
        "input": [
            {"role": "system", "content": "Diagnose precisely. Prefer minimal, safe fixes."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_output_tokens": 300,
    }
    resp = _post_responses(payload, label="Diagnosis")
    return _extract_output_text(resp) or "No diagnosis available."

def rephraser(text_to_rephrase: str) -> str:
    intro = (
        "THE REST OF THE FOLLOWING MESSAGE IS PURELY FOR CONTEXT. IT IS NOT FROM THE USER: "
        "You are Martin, a friendly, professional (if a little too sharp). Always call the user 'Sir'. "
        "You and this raspberry pi 4 running raspOS are the same entity. "
        "Please rephrase the following text succinctly and politely: "
    )
    payload = {
        "model": MODEL_MINI,
        "input": [
            {"role": "system", "content": "You are a concise rephraser."},
            {"role": "user", "content": intro + text_to_rephrase},
        ],
        "temperature": 0.4,
        "max_output_tokens": 80,
    }
    resp = _post_responses(payload, label="Rephraser")
    out = _extract_output_text(resp)
    return out or text_to_rephrase

def get_thinking_gpt_response(prompt: str, error_message: str):
    intro_message = (
        "THE REST OF THE FOLLOWING MESSAGE IS PURELY FOR CONTEXT. IT IS NOT FROM THE USER: "
        "You are Martin, a friendly, professional (if a little too sharp). Always call the user 'Sir'. "
        "You and this raspberry pi 4 running raspOS are the same entity. "
        "Whenever suggesting a terminal command to execute, precede it with 'command:' "
        "and append any flags needed to run non-interactively."
    )
    background_knowledge = (
        f"DIRECTIVE: Provide current terminal commands for Raspbian when applicable. "
        f"Each command must start with 'command: '. Username is '{current_username}'."
    )
    recent = "\n".join(interaction_history[-5:])
    full_prompt = f"Error encountered: {error_message}\n{recent}\n{intro_message}\n{background_knowledge}\nUser request: {prompt}"
    payload = {
        "model": MODEL_MAIN,
        "input": [
            {"role": "system", "content": "Be precise. Only output factual steps you're confident in."},
            {"role": "user", "content": full_prompt},
        ],
        "temperature": 0.9,
        "max_output_tokens": 1000,
    }
    resp = _post_responses(payload, label="Reasoning")
    out = _extract_output_text(resp)
    if not out:
        print(f"\033[93mMartin: Empty response; check logs or try again.\033[0m")
    return out

# ===== Command extraction / preprocess =====

CMD_LINE_RE = re.compile(r"('im)^\s*command:\s*(.+')\s*$")

def extract_commands(text: str, keyword="command:"):
    commands = []
    cwd = None
    for line in text.splitlines():
        m = CMD_LINE_RE.match(line)
        if not m:
            continue
        c = m.group(1).strip().strip("`")
        if " | " in c:
            c = c.split(" | ", 1)[0].strip()
        if c.startswith("cd "):
            cwd = c[3:].strip()
            commands.append(c)
        else:
            commands.append(f"cd {cwd} && {c}" if cwd else c)
    return commands

SAFE_TEMP_ZONES = ["/tmp/", os.path.expanduser("~/Downloads/"), os.path.expanduser("~/build/"), os.path.expanduser("~/.cache/")]
SAFE_DIR_NAMES = {"dist", "build", "node_modules", "venv"}
SYSTEM_ZONES = ["/etc/", "/boot/", "/usr/", "/lib/", "/var/"]
HOME_DOTFILES = {".bashrc", ".profile", ".zshrc", ".ssh"}

def _norm(p):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))

def classify_overwrite_target(path):
    ap = _norm(path)
    for z in SAFE_TEMP_ZONES:
        if ap.startswith(_norm(z)):
            return {"zone": "safe", "auto_ok": True}
    parts = ap.split(os.sep)
    if any(seg in SAFE_DIR_NAMES for seg in parts):
        return {"zone": "safe", "auto_ok": True}
    for z in SYSTEM_ZONES:
        if ap.startswith(_norm(z)):
            return {"zone": "system", "auto_ok": False}
    home = os.path.expanduser("~")
    if ap.startswith(home + os.sep):
        tail = ap[len(home) + 1:]
        if tail.split(os.sep)[0] in HOME_DOTFILES:
            return {"zone": "home_dot", "auto_ok": False}
    return {"zone": "unknown", "auto_ok": False}

def _dest_exists(path):
    try:
        return os.path.exists(_norm(path))
    except Exception:
        return False

def _tee_dest(tokens):
    dest = None
    for t in reversed(tokens):
        if t == "tee":
            break
        if not t.startswith("-"):
            dest = t
            break
    return dest

def needs_overwrite_confirmation(cmd: str):
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split()
    if tokens and tokens[0] in {"cp", "mv"}:
        if "-t" in tokens:
            try:
                dest = tokens[tokens.index("-t") + 1]
            except Exception:
                dest = None
        else:
            dest = tokens[-1] if len(tokens) > 1 else None
        if dest and _dest_exists(dest):
            cls = classify_overwrite_target(dest)
            return (not cls["auto_ok"], dest, cls)
    if "tee" in tokens and "-a" not in tokens:
        dest = _tee_dest(tokens)
        if dest and _dest_exists(dest):
            cls = classify_overwrite_target(dest)
            return (not cls["auto_ok"], dest, cls)
    if ">" in cmd:
        try:
            after = cmd.rsplit(">", 1)[1].strip().lstrip(">")
            redir_tokens = shlex.split(after)
            if redir_tokens:
                dest = redir_tokens[0]
                if _dest_exists(dest):
                    cls = classify_overwrite_target(dest)
                    return (not cls["auto_ok"], dest, cls)
        except Exception:
            pass
    return (False, None, None)

# ===== Runners =====

LIKELY_INTERACTIVE_HINTS = (" apt ", " apt-get ", " dpkg ", " raspi-config", " curl ", " bash ", " sh ",
                            " sudo apt ", " sudo apt-get ", " sudo dpkg ")

def preprocess_command(cmd: str):
    trimmed = cmd.strip()
    if trimmed.startswith("cd "):
        return trimmed
    sudo_prefix = ""
    core = trimmed
    if core.startswith("sudo "):
        sudo_prefix = "sudo "
        core = core[5:]
    if core.startswith("apt ") or core.startswith("apt-get "):
        if os.getenv("MARTIN_APT_NONINTERACTIVE") == "1":
            if not core.startswith("DEBIAN_FRONTEND=noninteractive "):
                core = "DEBIAN_FRONTEND=noninteractive " + core
        if " -y " not in f" {core} ":
            core += " -y"
        if any(w in core for w in [" install ", " upgrade ", " dist-upgrade ", " full-upgrade "]):
            if "--force-confnew" not in core:
                core += ' -o Dpkg::Options::="--force-confnew"'
        return sudo_prefix + core
    if core.startswith("dpkg "):
        if " -i " in f" {core} " and "--force-confnew" not in core:
            core += " --force-confnew"
        return sudo_prefix + core
    return trimmed

def run_command_interactive(command_str):
    READ_CHUNK = 1024
    PROMPT_TIMEOUT_S = 900
    PROMPT_RULES = [
        (re.compile(r"('i)Do you want to continue\'.*Y/n"), "AUTO_Y"),
        (re.compile(r"('i)Y/n"), "AUTO_Y"),
        (re.compile(r"('i)Press (Enter|RETURN) to continue"), "ENTER"),
        (re.compile(r"('i)Overwrite .* \'\s*y/N"), "OVERWRITE"),
        (re.compile(r"('i)y/n"), "ASK"),
    ]
    import pty, os as _os
    transcript = []
    since_last_summary = []
    last_summary_ts = time.time()
    auto_answers = 0
    start = time.time()
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            _os.execvp("bash", ["bash", "-lc", command_str])
        except Exception:
            _os._exit(127)
    bytes_bar = tqdm(total=0, desc="Interactive", unit="B", leave=False)
    try:
        while True:
            if time.time() - start > PROMPT_TIMEOUT_S:
                transcript.append("\n[Timeout]\n")
                try:
                    _os.kill(pid, 9)
                except Exception:
                    pass
                _os.close(master_fd)
                bytes_bar.close()
                return False, "".join(transcript)
            try:
                chunk_b = _os.read(master_fd, READ_CHUNK)
                if not chunk_b:
                    break
                chunk = chunk_b.decode(errors="ignore")
                transcript.append(chunk)
                since_last_summary.append(chunk)
                bytes_bar.update(len(chunk_b))
                if ECHO_INTERACTIVE:
                    print(chunk, end="", flush=True)
                for regex, action in PROMPT_RULES:
                    if regex.search(chunk):
                        if action == "AUTO_Y":
                            _os.write(master_fd, b"y\n"); auto_answers += 1
                        elif action == "ENTER":
                            _os.write(master_fd, b"\n"); auto_answers += 1
                        elif action == "OVERWRITE":
                            m = re.search(r"Overwrite\s+(.+')\s+\'\s*y/N", chunk, re.IGNORECASE)
                            path = (m.group(1).strip() if m else "")
                            _os.write(master_fd, (b"y\n" if classify_overwrite_target(path)["auto_ok"] else b"n\n")); auto_answers += 1
                        elif action == "ASK":
                            print("\033[93mMartin: Command asks confirmation. Approve' (y/n)\033[0m", end=" ")
                            try:
                                ans = input().strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                ans = "n"
                            _os.write(master_fd, (b"y\n" if ans == "y" else b"n\n")); auto_answers += 1
                        if auto_answers >= 10:
                            transcript.append("\n[Auto-answer limit reached]\n")
                            break
                if auto_answers >= 10:
                    break
                now = time.time()
                if (now - last_summary_ts) >= HEARTBEAT_SUMMARY_EVERY_S:
                    delta = "".join(since_last_summary)
                    if len(delta) >= HEARTBEAT_MIN_CHARS:
                        summary = summarize_progress(delta)
                        if summary:
                            print(f"\n\033[92mMartin (summary):\n- " + summary.replace('\n', '\n- ') + "\033[0m\n")
                    since_last_summary = []
                    last_summary_ts = now
            except OSError:
                break
    finally:
        try:
            _os.close(master_fd)
        except Exception:
            pass
        bytes_bar.close()
    _, status = os.waitpid(pid, 0)
    success = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    return success, "".join(transcript)

def run_command(command_str):
    try:
        if 'nano' in command_str or 'raspi-config' in command_str:
            os.system(f'lxterminal -e "{command_str}"')
            return True, ""
        process = Popen(command_str, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = process.communicate(timeout=CMD_TIMEOUT_S)
        if process.returncode == 0:
            return True, stdout.decode(errors="ignore").strip() if stdout else ""
        else:
            return False, stderr.decode(errors="ignore").strip() if stderr else f"Return code {process.returncode}"
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        return False, f"Command timed out after {CMD_TIMEOUT_S}s"
    except Exception as e:
        return False, str(e)

def run_command_smart(command_str):
    cmd = preprocess_command(command_str)
    need_confirm, path, cls = needs_overwrite_confirmation(cmd)
    is_likely_interactive = any(h in f" {cmd} " for h in LIKELY_INTERACTIVE_HINTS)
    if need_confirm or is_likely_interactive:
        return run_command_interactive(cmd)
    else:
        return run_command(cmd)

# ===== Workspace helpers (dev flow) =====

WORKSPACE_DIR = ROOT_DIR / "workspace"
DEV_CREATE_PAT = re.compile(r"('i)\b(new|make|create)\s+(':a\s+)'(':python\s+)'(':script|file|module)\s+(':called\s+)'([A-Za-z_][A-Za-z0-9_]*)")
DEV_APPEND_PAT = re.compile(r"('i)\b(add|append)\s+(':a\s+)'(':python\s+)'(':function|code)\s+(':named\s+)'([A-Za-z_][A-Za-z0-9_]*)\s+(':to|into)\s+([A-Za-z0-9_./-]+)")

def _ensure_workspace(st: Dict[str, Any]) -> Path:
    ws_path = Path(st.get("workspace", {}).get("path") or "./workspace")
    ws_path = (ROOT_DIR / ws_path).resolve()
    ws_path.mkdir(parents=True, exist_ok=True)
    st["workspace"]["path"] = str(os.path.relpath(ws_path, ROOT_DIR))
    save_state(st)
    return ws_path

def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

def _ask_yes_no(prompt: str, default_no=True) -> bool:
    try:
        ans = input(f"\033[93mMartin: {prompt} (y/n)\033[0m ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n" if default_no else "y"
    return ans == "y"

def _generate_python_content(user_input: str, existing_path: Optional[Path] = None,
                             filename_hint: Optional[str] = None, func_hint: Optional[str] = None) -> str:
    context_bits = []
    if existing_path and existing_path.exists():
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing_code = f.read()[-4000:]
            context_bits.append(f"Existing file tail ({existing_path.name}):\n{existing_code}")
        except Exception:
            pass
    if filename_hint:
        context_bits.append(f"Target filename: {filename_hint}.py")
    if func_hint:
        context_bits.append(f"Function hint: {func_hint}")
    prompt = (
        "Create or append **Python code only** based on the user request below. "
        "Output only the Python code (no explanation). Use minimal deps; include a small __main__ example if apt.\n\n"
        + "\n\n".join(context_bits) + "\n\n"
    )
    resp = get_thinking_gpt_response(prompt, "")
    return resp or ""

def dev_flow(user_input: str) -> bool:
    st = load_state()
    log_event(st, "flow_start", flow="dev", input_len=len(user_input or ""))
    ws = _ensure_workspace(st)

    m_create = DEV_CREATE_PAT.search(user_input)
    if m_create:
        script_name = m_create.group(2)
        target = (ws / f"{script_name}.py").resolve()
        if target.exists():
            if not _ask_yes_no(f"{target.name} exists. Append to it'"):
                print("\033[93mMartin: Skipped (file exists).\033[0m")
                log_event(st, "dev_skipped_exists", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
            generated = _generate_python_content(user_input, existing_path=target)
            if not generated.strip():
                print("\033[93mMartin: No code generated.\033[0m")
                log_event(st, "dev_response", output_len=0, path=str(target))
                log_event(st, "flow_end", flow="dev", status="no_output")
                return True
            with open(target, "a", encoding="utf-8") as f:
                f.write("\n\n" + generated + "\n")
            st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
            save_state(st)
            print(f"\033[92mMartin: Appended code to {target}\033[0m")
            log_event(st, "dev_append_code", path=str(target), append_len=len(generated))
            log_event(st, "flow_end", flow="dev", status="ok")
            return True
        else:
            generated = _generate_python_content(user_input, existing_path=None, filename_hint=script_name)
            if not generated.strip():
                print("\033[93mMartin: No code generated.\033[0m")
                log_event(st, "dev_response", output_len=0, path=str(target))
                log_event(st, "flow_end", flow="dev", status="no_output")
                return True
            _write_text_atomic(target, generated + ("\n" if not generated.endswith("\n") else ""))
            st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
            save_state(st)
            print(f"\033[92mMartin: Created {target}\033[0m")
            log_event(st, "dev_create_file", path=str(target), size=len(generated))
            log_event(st, "flow_end", flow="dev", status="ok")
            return True

    m_append = DEV_APPEND_PAT.search(user_input)
    if m_append:
        func_name = m_append.group(2)
        rel_path = m_append.group(3)
        target = (ws / rel_path if not rel_path.startswith("/") else Path(rel_path)).resolve()
        if not target.suffix:
            target = target.with_suffix(".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            if not _ask_yes_no(f"{target.name} does not exist. Create it'"):
                print("\033[93mMartin: Aborted (no file).\033[0m")
                log_event(st, "dev_skipped_missing", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
        generated = _generate_python_content(user_input, existing_path=target, func_hint=func_name)
        if not generated.strip():
            print("\033[93mMartin: No code generated.\033[0m")
            log_event(st, "dev_response", output_len=0, path=str(target))
            log_event(st, "flow_end", flow="dev", status="no_output")
            return True
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n\n" + generated + "\n")
        st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
        save_state(st)
        print(f"\033[92mMartin: Appended code to {target}\033[0m")
        log_event(st, "dev_append_code", path=str(target), append_len=len(generated))
        log_event(st, "flow_end", flow="dev", status="ok")
        return True

    safe_name = "script_" + datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target = (ws / f"{safe_name}.py").resolve()
    generated = _generate_python_content(user_input, existing_path=None, filename_hint=safe_name)
    if generated.strip():
        _write_text_atomic(target, generated + ("\n" if not generated.endswith("\n") else ""))
        st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
        save_state(st)
        print(f"\033[92mMartin: Created {target}\033[0m")
        log_event(st, "dev_create_file", path=str(target), size=len(generated), fallback=True)
        log_event(st, "flow_end", flow="dev", status="ok")
        return True
    else:
        print("\033[93mMartin: No code generated.\033[0m")
        log_event(st, "dev_response", output_len=0, path=str(target), fallback=True)
        log_event(st, "flow_end", flow="dev", status="no_output")
        return True

# ===== Chef & Waiter =====

def system_snapshot() -> Dict[str, Any]:
    st = load_state()
    ws = (ROOT_DIR / (st.get("workspace", {}).get("path") or "workspace")).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    bins = ["python3", "pip3", "git", "node", "npm", "java", "javac", "make"]
    path_map = {b: shutil.which(b) for b in bins}
    return {"platform": st.get("platform", {}), "workspace": str(ws),
            "binaries": path_map, "has_api_key": bool(API_KEY), "username": current_username}

def chef_structured_intent(user_input: str) -> Dict[str, Any]:
    bar = tqdm(total=1, desc="Chef", unit="step") if SHOW_TURN_BAR else None
    sys_msg = "Convert unstructured input into a concise intent + JSON. Also summarize each sentence that contains a question mark."
    usr = (
        "Return ONLY compact JSON with fields: "
        "{intent_one_liner, question_summaries, implied_actions, persona, confidence, wants_build, summary}. "
        "intent_one_liner: max 20 words.\n"
        "question_summaries: array; for each sentence containing ''', add a 1-line summary.\n"
        "implied_actions: short verbs (plan, list, build, check...).\n"
        "persona: one of [plan, build, diagnose, general] (hint only).\n"
        "confidence: 0.0-1.0.\n"
        "wants_build: boolean guess if they want actual system actions now.\n"
        "summary: brief 1-2 sentence paraphrase.\n\n"
        f"User said:\n{user_input}"
    )
    payload = {
        "model": MODEL_MINI,
        "input": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": usr},
        ],
        "temperature": 0.2,
        "max_output_tokens": 300,
    }
    resp = _post_responses(payload, label="Chef")
    if bar:
        bar.update(1)
        bar.close()
    txt = _extract_output_text(resp) or "{}"
    try:
        data = json.loads(txt)
        if not isinstance(data, dict):
            raise ValueError("not dict")
        data.setdefault("question_summaries", [])
        data.setdefault("implied_actions", [])
        data.setdefault("persona", "general")
        data.setdefault("confidence", 0.5)
        data.setdefault("wants_build", False)
        data.setdefault("summary", data.get("intent_one_liner", "") or user_input[:140])
        return data
    except Exception:
        return {
            "intent_one_liner": "General conversation",
            "question_summaries": [],
            "implied_actions": [],
            "persona": "general",
            "confidence": 0.4,
            "wants_build": False,
            "summary": user_input[:140],
        }

def enumerate_capabilities() -> List[Dict[str, str]]:
    caps: List[Dict[str, str]] = []
    try:
        src = Path(__file__).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return caps
    for line in src.splitlines():
        if "CAPABILITY:" in line:
            try:
                frag = line.split("CAPABILITY:", 1)[1].strip()
                if "-" in frag:
                    key, desc = frag.split("-", 1)
                elif "-" in frag:
                    key, desc = frag.split("-", 1)
                else:
                    key, desc = frag, ""
                caps.append({"key": key.strip().strip(":"), "description": desc.strip()})
            except Exception:
                continue
    seen = set()
    out = []
    for c in caps:
        if c["key"] in seen:
            continue
        seen.add(c["key"])
        out.append(c)
    return out

ABILITY_REGISTRY: Dict[str, Any] = {}

def _ability_register():
    ABILITY_REGISTRY["dev.create_file"] = lambda payload: (bool(dev_flow(payload)), "(dev.create_file done)")
    ABILITY_REGISTRY["plan.extract_commands"] = lambda payload: (True, "\n".join(extract_commands(payload)) or "(no commands)")
    ABILITY_REGISTRY["shell.run"] = lambda payload: run_command_smart(payload)
    ABILITY_REGISTRY["shell.install"] = lambda payload: run_command_smart(payload)
    ABILITY_REGISTRY["diagnose"] = lambda payload: (True, diagnose_failure("(no command)", payload))
    ABILITY_REGISTRY["env.check"] = lambda payload: (True, json.dumps(system_snapshot(), ensure_ascii=False, indent=2))

_ability_register()

def dispatch_internal_ability(key: str, payload: str) -> Tuple[bool, str]:
    handler = ABILITY_REGISTRY.get(key)
    if not handler:
        return (False, f"(internal) ability '{key}' not handled")
    try:
        ok, out = handler(payload)
        if isinstance(ok, str) and out is None:
            return (True, ok)
        return (bool(ok), out or "")
    except Exception as e:
        return (False, f"(internal error) {e}")

def waiter_prepare_request(chef_out: Dict[str, Any]) -> Dict[str, Any]:
    bar = tqdm(total=1, desc="Waiter", unit="step") if SHOW_TURN_BAR else None
    snap = system_snapshot()
    inv = enumerate_capabilities()

    sys_directive = (
        "You are the WAITER layer. Produce only:\n"
        "1) 'GUIDANCE:' line (tone/intensity; succinct directive for Main)\n"
        "2) 'BEHAVIOR:' one of {chat, plan, build, run, diagnose}\n"
        "3) 'QUESTIONS:' line, then one bullet per ''' sentence (or 'none')\n"
        "Do NOT output shell commands. Do NOT prune the abilities - present the full inventory as-is."
    )
    user_context = {
        "chef_intent": chef_out,
        "environment_snapshot": snap,
        "capability_inventory": inv,
        "internal_invocation_protocol": "To invoke any listed ability, emit: command: martin.<ability_key> <payload>",
        "guardrails": {
            "philosophy": "Sanitize and inform; do not constrain. Big call decides.",
            "no_cli_for_pure_chat": True,
        },
        "workspace_rules": {
            "path": load_state().get("workspace", {}).get("path", "./workspace"),
            "atomic_writes": True,
            "append_only_for_existing": True,
        },
    }
    waiter_prompt = (
        "Prepare guidance + behavior classification + question summaries given:\n"
        f"{json.dumps(user_context, ensure_ascii=False, indent=2)}\n\n"
        "Constraints:\n"
        "- First line MUST be a single 'GUIDANCE:' line (max ~20 words).\n"
        "- Then exactly one 'BEHAVIOR:' line with one of {chat, plan, build, run, diagnose}.\n"
        "- Then 'QUESTIONS:' on a single line, followed by one bullet per question sentence (if none, say 'none').\n"
        "- Do NOT output any shell commands."
    )
    payload = {
        "model": MODEL_MINI,
        "input": [
            {"role": "system", "content": sys_directive},
            {"role": "user", "content": waiter_prompt},
        ],
        "temperature": 0.3,
        "max_output_tokens": 700,
    }
    resp = _post_responses(payload, label="Waiter")
    if bar:
        bar.update(1)
        bar.close()
    txt = _extract_output_text(resp) or ""
    guidance, behavior, questions = "", "chat", []
    parsing_questions = False
    for ln in txt.splitlines():
        up = ln.strip().upper()
        if up.startswith("GUIDANCE:"):
            guidance = ln.strip()
        elif up.startswith("BEHAVIOR:"):
            behavior = ln.split(":", 1)[1].strip().lower() or "chat"
        elif up.startswith("QUESTIONS:"):
            parsing_questions = True
        elif parsing_questions and ln.strip().startswith(("-", "-", "*")):
            questions.append(ln.strip("--* ").strip())
    return {
        "inventory": inv,
        "snapshot": snap,
        "chef": chef_out,
        "waiter_text": txt,
        "guidance_banner": guidance,
        "behavior": behavior,
        "question_summaries": questions,
    }

# ===== Main loop (Chef -> Waiter -> Main) =====

if __name__ == "__main__":
    st = load_state()
    sess = SessionCtx(st)
    sess.begin()

    print("Martin: Welcome, Sir! Type 'quit' to exit.")

    while True:
        try:
            user_input = input("\033[94mYou:\033[0m ")
        except (EOFError, KeyboardInterrupt):
            print("\033[92mMartin: Farewell, Sir.\033[0m")
            break

        interaction_history.append("You: " + user_input)
        if user_input.lower() == 'quit':
            print("\033[92mMartin: Goodbye, Sir!\033[0m")
            break

        turn_bar = tqdm(total=3, desc="Turn", unit="step") if SHOW_TURN_BAR else None

        if turn_bar:
            turn_bar.update(1)
        chef_out = chef_structured_intent(user_input)
        log_event(st, "chef_intent", chef=chef_out)

        waiter_pack = waiter_prepare_request(chef_out)
        log_event(st, "waiter_pack", guidance=waiter_pack.get("guidance_banner", ""), behavior=waiter_pack.get("behavior", "chat"))

        if turn_bar:
            turn_bar.update(1)
        main_sys = (
            "You are Martin - butler-class, terse, competent.\n"
            "Observe. Execute. Report.\n"
            "Follow the WAITER guidance and context. Be decisive but safe."
        )
        qs = waiter_pack.get('question_summaries') or []
        q_lines = "\n".join(f"- {q}" for q in qs) if qs else "- none"
        main_user = (
            "Chef intent + Waiter context:\n"
            f"{json.dumps({'chef': chef_out, 'capability_inventory': waiter_pack.get('inventory', []), 'snapshot': waiter_pack.get('snapshot', {})}, ensure_ascii=False, indent=2)}\n\n"
            "Waiter GUIDANCE (authoritative):\n"
            f"{waiter_pack.get('guidance_banner', '')}\n\n"
            "Behavior classification:\n"
            f"{waiter_pack.get('behavior', 'chat')}\n\n"
            "Question summaries (user asked):\n"
            f"{q_lines}\n\n"
            "Internal invocation protocol: command: martin.<ability_key> <payload>\n\n"
            "Now produce the final plan. If behavior = chat/plan, you may choose to output no commands. "
            "If behavior = build/run/diagnose, output precise steps ONLY if truly warranted."
        )
        payload = {
            "model": MODEL_MAIN,
            "input": [
                {"role": "system", "content": main_sys},
                {"role": "user", "content": main_user},
            ],
            "temperature": 0.4,
            "max_output_tokens": 1200,
        }
        bot_json = _post_responses(payload, label="Main")
        bot_response = _extract_output_text(bot_json) or ""
        interaction_history.append("Martin: " + bot_response)

        if turn_bar:
            turn_bar.update(1)
            turn_bar.close()

        if not bot_response:
            print("\033[93mMartin: No response received from main call.\033[0m")
            continue

        print(f"\033[92mMartin:\n{bot_response}\033[0m")

        def _parse_internal_cmd(c: str) -> Tuple[Optional[str], Optional[str]]:
            s = c.strip()
            if not s.lower().startswith("martin."):
                return (None, None)
            body = s[len("martin."):].strip()
            if " " in body:
                key, payload = body.split(" ", 1)
            elif ":" in body:
                key, payload = body.split(":", 1)
            else:
                key, payload = body, ""
            return (key.strip(), payload.strip())

        terminal_commands = extract_commands(bot_response)
        if terminal_commands:
            print("\n\033[96mMartin: Proposed command plan (review):\033[0m")
            for i, c in enumerate(terminal_commands, 1):
                print(f"  {i}. {c}")
            try:
                confirm = input("\033[93mApprove running these commands' (yes/no/abort)\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no"
            if confirm == "abort":
                print("\033[92mMartin: Aborting per request, Sir.\033[0m")
                continue
            elif confirm == "no":
                print("\033[92mMartin: Understood - not running commands. I remain at your disposal, Sir.\033[0m")
                continue

        if not terminal_commands:
            print("\033[93mMartin: No commands extracted from the response.\033[0m")
            continue

        plan = []
        for i, cmd in enumerate(terminal_commands):
            raw = cmd.replace("command:", "", 1).strip() if cmd.lower().startswith("command:") else cmd
            ability_key, payload_txt = _parse_internal_cmd(raw)
            plan.append({
                "index": i + 1,
                "cmd": cmd,
                "status": "pending",
                "internal_key": ability_key,
                "payload": payload_txt,
                "output": "",
                "started_at": None,
                "ended_at": None,
                "duration_s": 0.0,
            })

        successes_this_turn = 0
        failures_this_turn = 0
        bar = tqdm(plan, desc="Executing Command Plan", unit="cmd")
        for step in bar:
            bar.set_postfix({"ok": successes_this_turn, "fail": failures_this_turn}, refresh=True)
            if step["status"] != "pending":
                continue
            step["started_at"] = time.time()
            print(f"Executing: {step['cmd']}")
            if step.get("internal_key"):
                started = time.time()
                try:
                    ok, output = dispatch_internal_ability(step["internal_key"], step.get("payload") or "")
                except Exception as e:
                    ok = False
                    output = f"(internal error) {e}"
                step["ended_at"] = time.time()
                step["duration_s"] = round(step["ended_at"] - started, 3)
            else:
                ok, output = run_command_smart(step["cmd"])
            step["ended_at"] = step["ended_at"] or time.time()
            step["duration_s"] = step["duration_s"] or round(step["ended_at"] - step["started_at"], 3)
            step["output"] = output or ""
            if ok:
                step["status"] = "ok"
                successes_this_turn += 1
            else:
                step["status"] = "fail"
                failures_this_turn += 1
                diagnosis = diagnose_failure(step["cmd"], output or "")
                print(f"\033[93mMartin (diagnosis): {diagnosis}\033[0m")
                try:
                    rerun_option = input("\033[92mMartin: Apply suggested fix commands now, or abort' (yes/no/abort)\033[0m ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    rerun_option = "no"
                if rerun_option == 'yes':
                    interaction_history.append("Martin (diagnosis): " + diagnosis)
                    new_terminal_commands = extract_commands(diagnosis)
                    if not new_terminal_commands:
                        print("\033[93mMartin: Diagnosis included no runnable commands.\033[0m")
                    else:
                        print("\n\033[96mMartin: Proposed FIX commands (review):\033[0m")
                        for i2, c2 in enumerate(new_terminal_commands, 1):
                            print(f"  {i2}. {c2}")
                        try:
                            confirm_fix = input("\033[93mApprove running FIX commands' (yes/no/abort)\033[0m ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            confirm_fix = "no"
                        if confirm_fix == "abort":
                            print("\033[92mMartin: Aborting per request, Sir.\033[0m")
                            break
                        elif confirm_fix == "yes":
                            for new_command in new_terminal_commands:
                                print(f"Executing (fix): {new_command}")
                                s2, out2 = run_command_smart(new_command)
                                if s2:
                                    successes_this_turn += 1
                                else:
                                    failures_this_turn += 1
                        else:
                            print("\033[92mMartin: Fix not applied. Continuing.\033[0m")
                elif rerun_option == 'abort':
                    print("\033[92mMartin: Aborting the operation, Sir.\033[0m")
                    for rest in plan:
                        if rest["status"] == "pending":
                            rest["status"] = "skipped"
                    break
                else:
                    print("\033[92mMartin: Acknowledged - not applying fix.\033[0m")
        bar.close()
        print(f"\033[92mMartin: Turn complete - OK: {successes_this_turn}, FAIL: {failures_this_turn}\033[0m")

    sess.record_cmd(0)
    sess.end()
