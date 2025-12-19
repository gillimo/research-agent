import os
import re
import shlex
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

# --- Constants for command preprocessing ---
CMD_LINE_RE = re.compile(r"^\s*command:\s*(.+)\s*$")

SAFE_TEMP_ZONES = ["/tmp/", os.path.expanduser("~/Downloads/"), os.path.expanduser("~/build/"), os.path.expanduser("~/.cache/")]
SAFE_DIR_NAMES = {"dist", "build", "node_modules", "venv"}
SYSTEM_ZONES = ["/etc/", "/boot/", "/usr/", "/lib/", "/var/"]
HOME_DOTFILES = {".bashrc", ".profile", ".zshrc", ".ssh"}

LIKELY_INTERACTIVE_HINTS = (" apt ", " apt-get ", " dpkg ", " raspi-config", " curl ", " bash ", " sh ",
                            " sudo apt ", " sudo apt-get ", " sudo dpkg ")

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
