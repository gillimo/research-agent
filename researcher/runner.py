import os
import re
import subprocess
import shlex
import time
from typing import Tuple, Optional, Dict, Any, List

from researcher.command_utils import (
    preprocess_command,
    needs_overwrite_confirmation,
    classify_overwrite_target,
    LIKELY_INTERACTIVE_HINTS # Used for heuristic to determine interactivity
)
from researcher.llm_utils import summarize_progress, diagnose_failure

# --- Constants (adapted from Martin) ---
CMD_TIMEOUT_S = 300
HEARTBEAT_SUMMARY_EVERY_S = 25
HEARTBEAT_MIN_CHARS = 600
ECHO_INTERACTIVE = False # To be controlled by researcher's config/verbosity

if os.name == "nt":
    _PTY_AVAILABLE = False
else:
    try:
        import pty
        _PTY_AVAILABLE = True
    except Exception:
        _PTY_AVAILABLE = False

# --- Runner functions (adapted from Martin) ---

def run_command_interactive(command_str: str) -> Tuple[bool, str]:
    """
    Runs a command in an interactive pseudo-terminal, handling common prompts.
    Provides progress summaries and auto-answers based on rules.
    """
    if not _PTY_AVAILABLE:
        return run_command(command_str)
    READ_CHUNK = 1024
    PROMPT_TIMEOUT_S = 900 # Longer timeout for interactive sessions
    PROMPT_RULES = [
        (re.compile(r"Do you want to continue'.*Y/n", re.IGNORECASE), "AUTO_Y"),
        (re.compile(r"Y/n", re.IGNORECASE), "AUTO_Y"),
        (re.compile(r"Press (Enter|RETURN) to continue", re.IGNORECASE), "ENTER"),
        (re.compile(r"Overwrite .* '\s*y/N", re.IGNORECASE), "OVERWRITE"),
        (re.compile(r"y/n", re.IGNORECASE), "ASK"),
    ]
    
    transcript: List[str] = []
    since_last_summary: List[str] = []
    last_summary_ts = time.time()
    auto_answers = 0
    start = time.time()

    pid, master_fd = pty.fork()
    if pid == 0: # Child process
        try:
            # Execute command using bash
            os.execvp("bash", ["bash", "-lc", command_str])
        except Exception:
            os._exit(127) # Exit with an error code if execvp fails
    
    # Parent process
    from tqdm import tqdm
    bytes_bar = tqdm(total=0, desc="Interactive", unit="B", leave=False) # Visual progress
    try:
        while True:
            # Check for timeout
            if time.time() - start > PROMPT_TIMEOUT_S:
                transcript.append("\n[Timeout]\n")
                try:
                    os.kill(pid, 9) # Kill child process
                except Exception:
                    pass
                os.close(master_fd)
                bytes_bar.close()
                return False, "".join(transcript)
            
            try:
                # Read output from child process
                chunk_b = os.read(master_fd, READ_CHUNK)
                if not chunk_b: # EOF - child process has exited
                    break
                
                chunk = chunk_b.decode(errors="ignore")
                transcript.append(chunk)
                since_last_summary.append(chunk)
                bytes_bar.update(len(chunk_b)) # Update progress bar

                if ECHO_INTERACTIVE: # If enabled, print output to console
                    print(chunk, end="", flush=True)
                
                # Check for prompts and auto-answer
                for regex, action in PROMPT_RULES:
                    if regex.search(chunk):
                        if action == "AUTO_Y":
                            os.write(master_fd, b"y\n"); auto_answers += 1
                        elif action == "ENTER":
                            os.write(master_fd, b"\n"); auto_answers += 1
                        elif action == "OVERWRITE":
                            m = re.search(r"Overwrite\s+(.+')\s+'\s*y/N", chunk, re.IGNORECASE)
                            path = (m.group(1).strip() if m else "")
                            # Use classify_overwrite_target for intelligent auto-answering
                            cls = classify_overwrite_target(path)
                            os.write(master_fd, (b"y\n" if cls.get("auto_ok") else b"n\n")); auto_answers += 1
                        elif action == "ASK":
                            # For prompts that need explicit user confirmation
                            print("\033[93mmartin: Command asks confirmation. Approve (y/n)\033[0m", end=" ")
                            try:
                                ans = input().strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                ans = "n"
                            os.write(master_fd, (b"y\n" if ans == "y" else b"n\n")); auto_answers += 1
                        
                        if auto_answers >= 10: # Limit auto-answers to prevent infinite loops
                            transcript.append("\n[Auto-answer limit reached]\n")
                            break
                if auto_answers >= 10:
                    break
                
                # Periodically summarize progress if enough new output
                now = time.time()
                if (now - last_summary_ts) >= HEARTBEAT_SUMMARY_EVERY_S:
                    delta = "".join(since_last_summary)
                    if len(delta) >= HEARTBEAT_MIN_CHARS:
                        summary = summarize_progress(delta)
                        if summary:
                            print(f"\n\033[92mmartin (summary):\n- " + summary.replace('\n', '\n- ') + "\033[0m\n")
                    since_last_summary = [] # Reset for next summary period
                    last_summary_ts = now
            except OSError: # Child process might have closed the master_fd
                break
    finally:
        try:
            os.close(master_fd) # Ensure master_fd is closed
        except Exception:
            pass
        bytes_bar.close() # Close progress bar

    # Wait for child process to exit and get status
    _, status = os.waitpid(pid, 0)
    success = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    return success, "".join(transcript)

def run_command(command_str: str) -> Tuple[bool, str]:
    """
    Runs a command non-interactively using subprocess.Popen.
    Handles timeouts and basic error capturing.
    """
    try:
        # Special handling for commands that might launch a GUI or block indefinitely without Popen
        # This part might need adjustment based on researcher's specific environment/needs.
        if 'nano' in command_str or 'raspi-config' in command_str:
            # Assuming a terminal emulator like 'lxterminal' is available in the environment
            # This is specific to Martin's original Raspberry Pi context.
            # For a general researcher, this might need to be a user-configurable action or removed.
            # For now, mimic original behavior if in a compatible environment.
            if os.name == 'posix' and os.getenv('DISPLAY'): # Check if graphical environment
                os.system(f'lxterminal -e "{command_str}"') # Example: runs in new terminal window
                return True, "" # Assume success for launching external GUI
            else:
                # If no graphical environment or lxterminal not available, fall back to Popen (might hang)
                pass # Continue to Popen below
        
        if os.name == "nt":
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", command_str],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            # Use shell=True for command string as it's often more convenient for complex commands
            # with pipes, redirection etc., but has security implications if command_str is untrusted.
            # Given command_str comes from LLM, it should be carefully sanitized (handled by command_utils).
            process = subprocess.Popen(command_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        stdout, stderr = process.communicate(timeout=CMD_TIMEOUT_S)
        
        # Decode output, ignoring errors for robustness
        stdout_str = stdout.decode(errors="ignore").strip() if stdout else ""
        stderr_str = stderr.decode(errors="ignore").strip() if stderr else ""

        if process.returncode == 0:
            return True, stdout_str
        else:
            # Return stderr if command failed, or a generic message if stderr is empty
            return False, stderr_str if stderr_str else f"Command failed with return code {process.returncode}"
    except subprocess.TimeoutExpired:
        try:
            process.kill() # Ensure process is terminated
        except Exception:
            pass
        return False, f"Command timed out after {CMD_TIMEOUT_S}s"
    except Exception as e:
        return False, str(e)

def run_command_capture(command_str: str) -> Tuple[bool, str, str, int]:
    """
    Runs a command non-interactively and captures stdout/stderr separately.
    Returns (ok, stdout, stderr, rc).
    """
    try:
        if os.name == "nt":
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", command_str],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            process = subprocess.Popen(command_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=CMD_TIMEOUT_S)
        stdout_str = stdout.decode(errors="ignore").strip() if stdout else ""
        stderr_str = stderr.decode(errors="ignore").strip() if stderr else ""
        rc = process.returncode
        return (rc == 0, stdout_str, stderr_str, rc)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        return (False, "", f"Command timed out after {CMD_TIMEOUT_S}s", 124)
    except Exception as e:
        return (False, "", str(e), 1)

def run_command_smart(command_str: str) -> Tuple[bool, str]:
    """
    Intelligently runs a command, deciding between interactive or non-interactive
    modes based on heuristics and overwrite safety checks.
    """
    cmd = preprocess_command(command_str) # Apply general preprocessing
    
    # Check for overwrite confirmation needs
    need_confirm, path, cls = needs_overwrite_confirmation(cmd)
    
    # Heuristic for likely interactive commands (e.g., package managers)
    is_likely_interactive = any(h in f" {cmd} " for h in LIKELY_INTERACTIVE_HINTS)
    
    if need_confirm or is_likely_interactive:
        # If user confirmation is needed or command is likely interactive, use interactive runner
        return run_command_interactive(cmd)
    else:
        # Otherwise, use non-interactive runner
        return run_command(cmd)

def run_command_smart_capture(command_str: str) -> Tuple[bool, str, str, int]:
    """
    Intelligently runs a command and captures stdout/stderr separately.
    For interactive runs, stdout is the transcript and stderr is empty.
    """
    cmd = preprocess_command(command_str)
    need_confirm, _path, _cls = needs_overwrite_confirmation(cmd)
    is_likely_interactive = any(h in f" {cmd} " for h in LIKELY_INTERACTIVE_HINTS)
    if need_confirm or is_likely_interactive:
        ok, transcript = run_command_interactive(cmd)
        return (ok, transcript, "", 0 if ok else 1)
    return run_command_capture(cmd)




def _extract_paths(cmd: str) -> list[str]:
    # Best-effort extraction of path-like tokens
    tokens = []
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split()
    paths = []
    for t in tokens:
        if ":\\\\" in t or t.startswith("/") or t.startswith("~"):
            paths.append(t.strip('"').strip("'"))
    return paths


def _looks_like_write(cmd: str) -> bool:
    lowered = cmd.lower()
    write_tokens = [
        " > ", ">>", "tee ", "set-content", "out-file",
        "del ", "erase ", "rm ", "rmdir", "mkdir",
        "cp ", "copy ", "mv ", "move ",
        "python -m pip install", "pip install",
    ]
    return any(t in lowered for t in write_tokens)


def enforce_sandbox(cmd: str, sandbox_mode: str, workspace: str) -> Tuple[bool, str]:
    """
    Returns (allowed, reason). Blocks obvious writes based on sandbox mode.
    """
    mode = (sandbox_mode or "workspace-write").lower()
    if mode == "full":
        return True, ""
    if not _looks_like_write(cmd):
        return True, ""
    if mode == "read-only":
        return False, "sandbox read-only: write command blocked"
    # workspace-write: allow writes only under workspace
    try:
        ws = os.path.abspath(os.path.expanduser(workspace))
        paths = _extract_paths(cmd)
        if not paths:
            return False, "sandbox workspace-write: write path not detected"
        for p in paths:
            try:
                ap = os.path.abspath(os.path.expanduser(p))
            except Exception:
                continue
            if ap.startswith(ws + os.sep):
                return True, ""
    except Exception:
        pass
    return False, "sandbox workspace-write: write outside workspace blocked"

# ===== Main for testing (if needed) =====
if __name__ == "__main__":
    # Example usage for testing purposes
    print("--- Testing run_command_smart ---")
    
    # Test a simple non-interactive command
    print("\nRunning 'echo Hello World':")
    success, output = run_command_smart("echo Hello World")
    print(f"Success: {success}, Output: '{output}'")

    # Test a command that might require overwrite confirmation (e.g., redirect)
    # Ensure this doesn't overwrite anything critical in your test environment
    test_file = "test_output.txt"
    if os.path.exists(test_file):
        os.remove(test_file)
    print(f"\nRunning 'echo first > {test_file}':")
    success, output = run_command_smart(f"echo first > {test_file}")
    print(f"Success: {success}, Output: '{output}'")
    
    print(f"\nRunning 'echo second > {test_file}' (should ask for overwrite if not auto_ok):")
    success, output = run_command_smart(f"echo second > {test_file}")
    print(f"Success: {success}, Output: '{output}'")
    if os.path.exists(test_file):
        os.remove(test_file)
    
    # Test an interactive-like command (apt update without -y) - will hang in non-interactive shell
    # This test is commented out to avoid hanging, but illustrates the intent.
    # print("\nRunning 'sudo apt update' (if run interactively, would prompt):")
    # success, output = run_command_smart("sudo apt update")
    # print(f"Success: {success}, Output: '{output}'")

    print("\n--- End Testing ---")
