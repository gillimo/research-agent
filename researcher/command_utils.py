import os
import re
import shlex
import tempfile
import subprocess
import hashlib
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List

# --- Constants for command preprocessing ---
CMD_LINE_RE = re.compile(r"^\s*command:\s*(.+)\s*$")

SAFE_TEMP_ZONES = ["/tmp/", os.path.expanduser("~/Downloads/"), os.path.expanduser("~/build/"), os.path.expanduser("~/.cache/")]
SAFE_DIR_NAMES = {"dist", "build", "node_modules", "venv"}
SYSTEM_ZONES = ["/etc/", "/boot/", "/usr/", "/lib/", "/var/"]
HOME_DOTFILES = {".bashrc", ".profile", ".zshrc", ".ssh"}

LIKELY_INTERACTIVE_HINTS = (" apt ", " apt-get ", " dpkg ", " raspi-config", " curl ", " bash ", " sh ",
                            " sudo apt ", " sudo apt-get ", " sudo dpkg ")

# High-risk command fragments and path hints.
_HIGH_RISK_FRAGMENTS = (
    " rm -rf", " rm -r", " del /s", " del /f", " rmdir /s", " rd /s",
    " format ", " diskpart", " mkfs", " dd if=", " shutdown", " reboot",
    " stop-computer", " restart-computer", " bcdedit", " reg delete", " reg add",
    " remove-item -recurse", " remove-item -force", " takeown", " icacls ",
    " chmod 777", " chown ", " git reset --hard", " git clean -fd", " git checkout --",
)
_SYSTEM_PATH_HINTS = ("/etc/", "/boot/", "/usr/", "/lib/", "/var/", "c:\\windows", "c:\\program files")

# --- Path utility ---
def _norm(p: str) -> str:
    """Normalizes a path by expanding user, vars, and converting to absolute path."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))

# --- Command extraction ---
def extract_commands(text: str) -> list[str]:
    """
    Extracts 'command:' lines from a given text.
    Handles 'cd' commands to build full paths for subsequent commands.
    """
    commands = []
    cwd: Optional[str] = None
    for line in text.splitlines():
        if "```" in line and "command:" in line:
            line = line.replace("```", "")
        m = CMD_LINE_RE.match(line)
        if not m:
            continue
        c = m.group(1).strip().strip("`") # Remove backticks if present
        # Handle cases where command might be piped, take only the first part
        if " | " in c and not c.startswith('"') and not c.startswith("'"): # Avoid splitting within quoted commands
             c = c.split(" | ", 1)[0].strip()

        if c.startswith("cd "):
            cwd = c[3:].strip()
            # Normalize cwd immediately for consistent absolute path calculations later
            # (though the command itself should still be 'cd <path>')
            commands.append(c)
        else:
            # If a cwd was set by a previous 'cd' command, prepend it
            if cwd:
                # Ensure the command can be executed from the new CWD.
                # For safety, ensure cwd is an absolute path for consistent behavior.
                abs_cwd = _norm(cwd)
                commands.append(f"cd {shlex.quote(abs_cwd)} && {c}")
            else:
                commands.append(c)
    return commands

# --- Overwrite safety checks ---
def _dest_exists(path: str) -> bool:
    """Checks if a normalized path exists."""
    try:
        return os.path.exists(_norm(path))
    except Exception:
        return False

def _tee_dest(tokens: list[str]) -> Optional[str]:
    """Extracts the destination from a 'tee' command."""
    dest = None
    for t in reversed(tokens):
        if t == "tee":
            break
        if not t.startswith("-"): # Assuming destination is not an option
            dest = t
            break
    return dest

def classify_overwrite_target(path: str) -> Dict[str, Any]:
    """
    Classifies a path based on its location to determine overwrite safety.
    Returns a dict with 'zone' and 'auto_ok' boolean indicating if overwrite is safe.
    """
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
        # Check for dotfiles in home directory
        tail = ap[len(home) + 1:]
        if tail and tail.split(os.sep)[0] in HOME_DOTFILES:
            return {"zone": "home_dot", "auto_ok": False}
    return {"zone": "unknown", "auto_ok": False}


def needs_overwrite_confirmation(cmd: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Determines if a command might overwrite an existing file in a non-safe zone,
    thus requiring user confirmation.
    Returns (needs_confirmation, target_path, classification).
    """
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split() # Fallback for malformed shlex input

    # cp, mv commands
    if tokens and tokens[0] in {"cp", "mv"}:
        dest_idx = -1
        if "-t" in tokens: # target directory specified
            try:
                dest_idx = tokens.index("-t") + 1
            except ValueError:
                pass # Fall through, dest_idx remains -1
        
        dest = None
        if dest_idx != -1 and dest_idx < len(tokens):
            dest = tokens[dest_idx]
        elif len(tokens) > 1: # Default to last argument as destination
            dest = tokens[-1]

        if dest and _dest_exists(dest):
            cls = classify_overwrite_target(dest)
            return (not cls["auto_ok"], dest, cls)

    # tee command (without -a for append)
    if "tee" in tokens and "-a" not in tokens:
        dest = _tee_dest(tokens)
        if dest and _dest_exists(dest):
            cls = classify_overwrite_target(dest)
            return (not cls["auto_ok"], dest, cls)

    # Redirection (>)
    if ">" in cmd:
        try:
            # Check for single or double redirection
            if ">>" in cmd:
                # Append redirection, not overwrite
                pass 
            else: # Overwrite redirection
                after_redir = cmd.rsplit(">", 1)[1].strip()
                redir_tokens = shlex.split(after_redir)
                if redir_tokens:
                    dest = redir_tokens[0]
                    if _dest_exists(dest):
                        cls = classify_overwrite_target(dest)
                        return (not cls["auto_ok"], dest, cls)
        except Exception:
            pass # Malformed command, ignore

    return (False, None, None)

# --- Command preprocessing ---
def preprocess_command(cmd: str) -> str:
    """
    Preprocesses a command string to make it non-interactive and more robust.
    Handles 'sudo', 'apt', 'dpkg' commands specifically.
    """
    trimmed = cmd.strip()
    # Handle 'cd' commands (Martin's original preprocess_command had a special case for it
    # but extract_commands already handles it by prepending 'cd <path> &&'.
    # Keeping this check here for robustness if cmd is called directly with 'cd'.
    if trimmed.startswith("cd "):
        return trimmed
    
    sudo_prefix = ""
    core = trimmed
    if core.startswith("sudo "):
        sudo_prefix = "sudo "
        core = core[5:] # Remove 'sudo ' for core logic

    # Specific handling for apt commands
    if core.startswith("apt ") or core.startswith("apt-get "):
        # Make apt non-interactive if MARTIN_APT_NONINTERACTIVE is set
        if os.getenv("MARTIN_APT_NONINTERACTIVE") == "1":
            if not core.startswith("DEBIAN_FRONTEND=noninteractive "):
                core = "DEBIAN_FRONTEND=noninteractive " + core
        # Add -y flag if not already present for automatic 'yes' to prompts
        if " -y " not in f" {core} ":
            core += " -y"
        # Force new config files for install/upgrade operations
        if any(w in core for w in [" install ", " upgrade ", " dist-upgrade ", " full-upgrade "]):
            if "--force-confnew" not in core:
                core += ' -o Dpkg::Options::="--force-confnew"'
        return sudo_prefix + core
    
    # Specific handling for dpkg commands
    if core.startswith("dpkg "):
        # Force new config files for install operations
        if " -i " in f" {core} " and "--force-confnew" not in core:
            core += " --force-confnew"
        return sudo_prefix + core
    
    return trimmed


def classify_command_risk(
    cmd: str,
    allowlist: Optional[list[str]] = None,
    denylist: Optional[list[str]] = None
) -> Dict[str, Any]:
    """
    Classifies command risk using heuristics and allow/deny lists.
    Returns dict: level (low|medium|high|blocked) and reasons list.
    """
    reasons: list[str] = []
    allowlist = [s.lower() for s in (allowlist or []) if s]
    denylist = [s.lower() for s in (denylist or []) if s]
    raw = cmd.strip()
    lowered = f" {raw.lower()} "

    for token in denylist:
        if token and token in lowered:
            return {"level": "blocked", "reasons": [f"denylist:{token}"]}

    for token in allowlist:
        if token and token in lowered:
            return {"level": "low", "reasons": [f"allowlist:{token}"]}

    for frag in _HIGH_RISK_FRAGMENTS:
        if frag in lowered:
            reasons.append(f"high-risk:{frag.strip()}")

    for hint in _SYSTEM_PATH_HINTS:
        if hint in lowered:
            reasons.append(f"system-path:{hint}")

    needs_overwrite, target, cls = needs_overwrite_confirmation(raw)
    if needs_overwrite and target:
        reasons.append(f"overwrite:{cls['zone']}")

    if reasons:
        level = "high" if any(r.startswith("high-risk") or r.startswith("system-path") for r in reasons) else "medium"
        return {"level": level, "reasons": reasons}

    return {"level": "low", "reasons": []}


def edit_commands_in_editor(commands: List[str]) -> List[str]:
    """Open a temp file for editing commands and return the updated list."""
    if not commands:
        return []
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if os.name == "nt" and not editor:
        editor = "notepad"
    if not editor:
        editor = "vi"
    content = "\n".join(commands) + "\n"
    pre_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".cmds", encoding="utf-8") as f:
        path = f.name
        f.write(content)
    try:
        subprocess.run([editor, path], check=False)
        with open(path, "r", encoding="utf-8") as f:
            edited_content = f.read()
        post_hash = hashlib.sha256(edited_content.encode("utf-8")).hexdigest()
        try:
            from researcher.state_manager import log_event, load_state
            log_event(load_state(), "editor_commands", path=path, pre_hash=pre_hash, post_hash=post_hash, changed=pre_hash != post_hash)
        except Exception:
            pass
        lines = [ln.strip() for ln in edited_content.splitlines()]
        updated = [ln for ln in lines if ln]
        return updated or commands
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def edit_commands_inline(commands: List[str]) -> List[str]:
    """Inline editor for commands using a simple text buffer prompt."""
    if not commands:
        return []
    print("\033[96mmartin: Inline editor\033[0m")
    print("Edit the command list below. Finish with a single line: .save (or .abort to cancel).")
    print("--- current commands ---")
    for line in commands:
        print(line)
    print("--- enter updated commands ---")
    updated: List[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return commands
        marker = line.strip().lower()
        if marker == ".save":
            break
        if marker == ".abort":
            return commands
        if line.strip():
            updated.append(line.strip())
    return updated or commands
