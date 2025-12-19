import os
import re
import datetime
import difflib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from researcher.state_manager import load_state, save_state, log_event, ROOT_DIR
from researcher.config_loader import load_config
from researcher.llm_utils import get_thinking_gpt_response

# --- Constants (adapted from Martin) ---
# WORKSPACE_DIR will be determined dynamically by _ensure_workspace
DEV_CREATE_PAT = re.compile(r"^\s*(new|make|create)\s+(a\s+)?(python\s+)?(script|file|module)\s+(called\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*$", re.IGNORECASE)
DEV_APPEND_PAT = re.compile(r"^\s*(add|append)\s+(a\s+)?(python\s+)?(function|code)\s+(named\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+(to|into)\s+([A-Za-z0-9_./-]+)\s*$", re.IGNORECASE)

# --- Workspace helpers (adapted from Martin) ---
def _ensure_workspace(st: Dict[str, Any]) -> Path:
    """Ensures the workspace directory exists and updates state."""
    # Martin's original code uses st.get("workspace", {}).get("path") or "./workspace"
    # We will use "./workspace" as the default relative to ROOT_DIR
    ws_path = Path(st.get("workspace", {}).get("path") or "workspace")
    ws_path = (ROOT_DIR / ws_path).resolve() # Resolve to an absolute path
    ws_path.mkdir(parents=True, exist_ok=True)
    
    # Store relative path in state for portability, but use absolute path for operations
    st["workspace"]["path"] = str(os.path.relpath(ws_path, ROOT_DIR))
    save_state(st)
    return ws_path

def _write_text_atomic(path: Path, text: str) -> None:
    """Writes text to a file atomically by writing to a temp file and then renaming."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

def _ask_yes_no(prompt: str, default_no: bool = True) -> bool:
    """Asks the user a yes/no question."""
    try:
        ans = input(f"\033[93mmartin: {prompt} (y/n)\033[0m ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n" if default_no else "y"
    return ans == "y"

def _auto_apply_enabled() -> bool:
    if os.environ.get("MARTIN_AUTO_APPLY", "").strip() == "1":
        return True
    try:
        cfg = load_config()
        policy = (cfg.get("execution", {}).get("approval_policy") or "").lower()
        return policy == "never"
    except Exception:
        return False

def _preview_and_confirm(path: Path, before: str, after: str) -> bool:
    diff_lines = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=str(path),
        tofile=str(path),
        lineterm="",
    ))
    if not diff_lines:
        return True
    max_lines = 200
    if len(diff_lines) > max_lines:
        head = diff_lines[:120]
        tail = diff_lines[-80:]
        print("\033[96mmartin: Diff preview (truncated)\033[0m")
        print("\n".join(head))
        print("... [diff truncated] ...")
        print("\n".join(tail))
    else:
        print("\033[96mmartin: Diff preview\033[0m")
        print("\n".join(diff_lines))
    if _auto_apply_enabled():
        return True
    return _ask_yes_no("Apply these changes?")

def _generate_python_content(user_input: str, existing_path: Optional[Path] = None,
                             filename_hint: Optional[str] = None, func_hint: Optional[str] = None) -> str:
    """
    Generates Python code based on user input, using the LLM.
    Provides context about existing files or hints.
    """
    context_bits = []
    if existing_path and existing_path.exists():
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing_code = f.read()[-4000:] # Read tail for context
            context_bits.append(f"Existing file tail ({existing_path.name}):\n```python\n{existing_code}\n```")
        except Exception:
            pass # Unable to read file, proceed without it
    if filename_hint:
        context_bits.append(f"Target filename: {filename_hint}.py")
    if func_hint:
        context_bits.append(f"Function hint: {func_hint}")
    
    # Construct the prompt for the LLM
    prompt_context = "\n\n".join(context_bits)
    prompt_template = (
        "Create or append **Python code only** based on the user request below. "
        "Output only the Python code (no explanation). Use minimal deps; include a small __main__ example if apt.\n"
        f"{prompt_context}\n\n"
        f"User request: {user_input}\n"
        "Provide ONLY the Python code block, no surrounding text or markdown." # Emphasize pure code output
    )
    
    # Call the LLM to generate content
    # Note: get_thinking_gpt_response needs to be adapted to accept a direct prompt,
    # or this will need to be re-evaluated for how the LLM is called here.
    # For now, it passes the full context as 'prompt' and empty error_message.
    generated_code = get_thinking_gpt_response(prompt_template, "")
    return generated_code


def dev_flow(user_input: str) -> bool:
    """
    Orchestrates the development flow: creating or appending Python files.
    """
    st = load_state()
    log_event(st, "flow_start", flow="dev", input_len=len(user_input or ""))
    ws = _ensure_workspace(st)

    m_create = DEV_CREATE_PAT.search(user_input)
    if m_create:
        script_name = m_create.group(6) # The matched script name
        target = (ws / f"{script_name}.py").resolve()
        
        if target.exists():
            if not _ask_yes_no(f"File '{target.name}' exists. Do you want to append to it?"):
                print("\033[93mmartin: Skipped (file exists and not appending).\033[0m")
                log_event(st, "dev_skipped_exists", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
            
            # Append to existing file
            generated = _generate_python_content(user_input, existing_path=target)
            if not generated.strip():
                print("\033[93mmartin: No code generated to append.\033[0m")
                log_event(st, "dev_response", output_len=0, path=str(target))
                log_event(st, "flow_end", flow="dev", status="no_output")
                return True
            
            try:
                before = target.read_text(encoding="utf-8")
            except Exception:
                before = ""
            after = before + ("\n\n" + generated + "\n")
            if not _preview_and_confirm(target, before, after):
                print("\033[93mmartin: Skipped (change not approved).\033[0m")
                log_event(st, "dev_skipped_not_approved", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
            with open(target, "a", encoding="utf-8") as f:
                f.write("\n\n" + generated + "\n")
            
            st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
            save_state(st)
            print(f"\033[92mmartin: Appended code to '{target}'.\033[0m")
            log_event(st, "dev_append_code", path=str(target), append_len=len(generated))
            log_event(st, "flow_end", flow="dev", status="ok")
            return True
        else:
            # Create new file
            generated = _generate_python_content(user_input, existing_path=None, filename_hint=script_name)
            if not generated.strip():
                print("\033[93mmartin: No code generated to create file.\033[0m")
                log_event(st, "dev_response", output_len=0, path=str(target))
                log_event(st, "flow_end", flow="dev", status="no_output")
                return True
            
            before = ""
            after = generated + ("\n" if not generated.endswith("\n") else "")
            if not _preview_and_confirm(target, before, after):
                print("\033[93mmartin: Skipped (change not approved).\033[0m")
                log_event(st, "dev_skipped_not_approved", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
            _write_text_atomic(target, after)
            st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
            save_state(st)
            print(f"\033[92mmartin: Created '{target}'.\033[0m")
            log_event(st, "dev_create_file", path=str(target), size=len(generated))
            log_event(st, "flow_end", flow="dev", status="ok")
            return True

    m_append = DEV_APPEND_PAT.search(user_input)
    if m_append:
        func_name = m_append.group(6) # The matched function name
        rel_path = m_append.group(8)  # The matched path
        target = (ws / rel_path if not rel_path.startswith("/") else Path(rel_path)).resolve()
        
        if not target.suffix: # If no suffix, assume .py
            target = target.with_suffix(".py")
        
        target.parent.mkdir(parents=True, exist_ok=True) # Ensure parent directories exist

        if not target.exists():
            if not _ask_yes_no(f"File '{target.name}' does not exist. Do you want to create it?"):
                print("\033[93mmartin: Aborted (file does not exist and not creating).\033[0m")
                log_event(st, "dev_skipped_missing", path=str(target))
                log_event(st, "flow_end", flow="dev", status="skipped")
                return True
        
        generated = _generate_python_content(user_input, existing_path=target, func_hint=func_name)
        if not generated.strip():
            print("\033[93mmartin: No code generated to append.\033[0m")
            log_event(st, "dev_response", output_len=0, path=str(target))
            log_event(st, "flow_end", flow="dev", status="no_output")
            return True
        
        try:
            before = target.read_text(encoding="utf-8")
        except Exception:
            before = ""
        after = before + ("\n\n" + generated + "\n")
        if not _preview_and_confirm(target, before, after):
            print("\033[93mmartin: Skipped (change not approved).\033[0m")
            log_event(st, "dev_skipped_not_approved", path=str(target))
            log_event(st, "flow_end", flow="dev", status="skipped")
            return True
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n\n" + generated + "\n")
        
        st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
        save_state(st)
        print(f"\033[92mmartin: Appended code to '{target}'.\033[0m")
        log_event(st, "dev_append_code", path=str(target), append_len=len(generated))
        log_event(st, "flow_end", flow="dev", status="ok")
        return True

    # Fallback: if no specific pattern matches, try to create a generic script
    safe_name = "script_" + datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target = (ws / f"{safe_name}.py").resolve()
    generated = _generate_python_content(user_input, existing_path=None, filename_hint=safe_name)
    if generated.strip():
        before = ""
        after = generated + ("\n" if not generated.endswith("\n") else "")
        if not _preview_and_confirm(target, before, after):
            print("\033[93mmartin: Skipped (change not approved).\033[0m")
            log_event(st, "dev_skipped_not_approved", path=str(target))
            log_event(st, "flow_end", flow="dev", status="skipped")
            return True
        _write_text_atomic(target, after)
        st["workspace"]["last_file"] = str(os.path.relpath(target, ROOT_DIR))
        save_state(st)
        print(f"\033[92mmartin: Created '{target}'.\033[0m")
        log_event(st, "dev_create_file", path=str(target), size=len(generated), fallback=True)
        log_event(st, "flow_end", flow="dev", status="ok")
        return True
    else:
        print("\033[93mmartin: No code generated (fallback).\033[0m")
        log_event(st, "dev_response", output_len=0, path=str(target), fallback=True)
        log_event(st, "flow_end", flow="dev", status="no_output")
        return True
