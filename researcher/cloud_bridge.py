import hashlib
import os
import shlex
import subprocess
import json # Added for API calls
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any


from researcher import sanitize
# from researcher.log_utils import setup_logger, log_event # Removed old log_utils import
from researcher.state_manager import log_event, load_state # New import for state_manager logging
from researcher.llm_utils import _post_responses, MODEL_MAIN, MODEL_MINI, HEADERS, TIMEOUT_S, MAX_RETRIES, BACKOFF_BASE_S # Reusing API call logic

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _template_has_unsafe_chars(template: str) -> bool:
    lowered = template.lower()
    if "$(" in lowered or "&&" in lowered or "||" in lowered:
        return True
    blocked = ["|", ";", ">", "<", "`", "&"]
    if os.name == "nt":
        blocked.append("^")
    return any(ch in template for ch in blocked)


def _split_cmd_template(cmd: str) -> Optional[list[str]]:
    try:
        argv = shlex.split(cmd, posix=os.name != "nt")
        if os.name == "nt":
            cleaned = []
            for arg in argv:
                if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"':
                    cleaned.append(arg[1:-1])
                else:
                    cleaned.append(arg)
            argv = cleaned
        return argv
    except ValueError:
        return None

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _append_cloud_log(logs_root: Optional[Path], event: str, **data) -> None:
    if not logs_root:
        return
    try:
        logs_root.mkdir(parents=True, exist_ok=True)
        path = logs_root / "cloud.ndjson"
        entry = {"ts": _now_iso(), "event": event, "data": data}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def allow_prompt(prompt: str) -> bool:
    """Simple allowlist: block obvious command execution hints."""
    lowered = prompt.lower()
    # Expanded list of blocked tokens for more robust guardrails
    blocked = any(token in lowered for token in [
        "command:", "rm ", "del ", "format c:", "sudo", "apt-get", "chmod", "chown", 
        "mv / ", "cp / ", "dd ", "mkfs", "wipefs", "reboot", "shutdown"
    ])
    return not blocked


@dataclass
class CloudCallResult:
    ok: bool
    output: str
    error: str
    rc: int
    sanitized: str
    changed: bool
    hash: str


def call_cloud(prompt: str, cmd_template: Optional[str] = None, logs_root: Optional[Path] = None, timeout: int = 60) -> CloudCallResult:
    """
    Makes a call to a cloud LLM, either via direct API or a shell command template.
    Uses sanitized prompt and structured logging.
    """
    st = load_state() # Load state for logging
    local_only = os.environ.get("RESEARCHER_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes"}
    if local_only:
        log_event(st, "cloud_call_blocked", reason="local_only", sanitized_prompt="[blocked]")
        _append_cloud_log(logs_root, "cloud_call_blocked", redacted=True, sanitized="[blocked]")
        return CloudCallResult(False, "", "blocked by local-only mode", 1, "[blocked]", True, _hash("[blocked]"))

    # Ensure logs_root exists if provided, and setup logger for cloud logs (still using old for now)
    # The cloud logger in llm_utils._post_responses will handle its own logging
    if logs_root:
        logs_root.mkdir(parents=True, exist_ok=True)
        # cloud_logger = setup_logger(logs_root / "cloud.log", name="researcher.cloud") # If we want a separate cloud logger

    sanitized, changed = sanitize.sanitize_prompt(prompt)
    if not allow_prompt(sanitized):
        log_event(st, "cloud_call_blocked", reason="allowlist", sanitized_prompt=sanitized)
        _append_cloud_log(logs_root, "cloud_call_blocked", redacted=changed, sanitized=sanitized)
        return CloudCallResult(False, "", "blocked by allowlist", 1, sanitized, changed, _hash(sanitized))

    # --- Cloud Provider Configuration ---
    cloud_provider = os.environ.get("RESEARCHER_CLOUD_PROVIDER", "openai").lower()
    cloud_model = os.environ.get("RESEARCHER_CLOUD_MODEL", MODEL_MAIN) # Default to main local model if not specified
    cloud_api_key = os.environ.get("RESEARCHER_CLOUD_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip()

    effective_headers = HEADERS.copy()
    effective_headers["Authorization"] = f"Bearer {cloud_api_key}"

    hashed_prompt = _hash(sanitized)
    log_event(st, "cloud_call_start", hash=hashed_prompt, redacted=changed, provider=cloud_provider, model=cloud_model)
    _append_cloud_log(
        logs_root,
        "cloud_call_start",
        hash=hashed_prompt,
        redacted=changed,
        provider=cloud_provider,
        model=cloud_model,
        sanitized=sanitized,
    )

    # --- Direct API Call (preferred) ---
    if cloud_api_key and cloud_provider:
        # Construct payload similar to llm_utils for chat completions
        payload = {
            "model": cloud_model,
            "input": [
                {"role": "user", "content": sanitized},
            ],
            "temperature": 0.7, # Default temperature for cloud calls
            "max_output_tokens": 1000, # Default max tokens for cloud calls
        }
        
        # We need a variant of _post_responses that can take custom headers/url for cloud
        # For now, we will adapt _post_responses or inline its logic here.
        # It's better to make _post_responses in llm_utils more flexible to accept custom API_KEY and URL.
        # For this pass, we will create a dedicated _post_cloud_responses
        
        cloud_resp = _post_cloud_responses(payload, provider=cloud_provider, api_key=cloud_api_key, timeout=timeout)
        
        if "output_text" in cloud_resp:
            output = cloud_resp["output_text"]
            log_event(st, "cloud_call_end", hash=hashed_prompt, rc=0, output_len=len(output), error=None)
            _append_cloud_log(logs_root, "cloud_call_end", hash=hashed_prompt, rc=0, output_len=len(output), error=None)
            return CloudCallResult(True, output, "", 0, sanitized, changed, hashed_prompt)
        elif "error" in cloud_resp:
            error_msg = cloud_resp["error"].get("message", "Unknown cloud API error")
            log_event(st, "cloud_call_end", hash=hashed_prompt, rc=1, output_len=0, error=error_msg)
            _append_cloud_log(logs_root, "cloud_call_end", hash=hashed_prompt, rc=1, output_len=0, error=error_msg)
            return CloudCallResult(False, "", error_msg, 1, sanitized, changed, hashed_prompt)

    # --- Fallback to CMD Template (if no direct API config or failed) ---
    if cmd_template:
        template_hash = _hash(cmd_template)
        log_event(st, "cloud_call_fallback_cmd", hash=hashed_prompt, template_hash=template_hash)
        _append_cloud_log(logs_root, "cloud_call_fallback_cmd", hash=hashed_prompt, template_hash=template_hash)
        if _template_has_unsafe_chars(cmd_template):
            log_event(st, "cloud_call_blocked", reason="cmd_template_unsafe", template_hash=template_hash)
            _append_cloud_log(logs_root, "cloud_call_blocked", redacted=changed, sanitized=sanitized, reason="cmd_template_unsafe", template_hash=template_hash)
            return CloudCallResult(False, "", "cmd_template contains unsafe shell characters", 1, sanitized, changed, hashed_prompt)
        cmd = cmd_template.replace("{prompt}", sanitized)
        argv = _split_cmd_template(cmd)
        if not argv:
            log_event(st, "cloud_call_blocked", reason="cmd_template_parse_failed", template_hash=template_hash)
            _append_cloud_log(logs_root, "cloud_call_blocked", redacted=changed, sanitized=sanitized, reason="cmd_template_parse_failed", template_hash=template_hash)
            return CloudCallResult(False, "", "cmd_template parse failed", 1, sanitized, changed, hashed_prompt)
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout.strip()
            error = proc.stderr.strip()
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            output, error, rc = "", "cloud command timed out", 124
        
        log_event(st, "cloud_call_end", hash=hashed_prompt, rc=rc, output_len=len(output), error=error)
        _append_cloud_log(logs_root, "cloud_call_end", hash=hashed_prompt, rc=rc, output_len=len(output), error=error)
        return CloudCallResult(rc == 0, output, error, rc, sanitized, changed, hashed_prompt)
    
    # If neither API nor cmd_template could be used
    log_event(st, "cloud_call_fail_no_config", hash=hashed_prompt, reason="No cloud API key/provider or cmd_template")
    _append_cloud_log(logs_root, "cloud_call_fail_no_config", hash=hashed_prompt)
    return CloudCallResult(False, "", "No cloud API key/provider or command template provided", 1, sanitized, changed, hashed_prompt)


def _post_cloud_responses(payload: Dict[str, Any], provider: str, api_key: str, timeout: int = TIMEOUT_S) -> Dict[str, Any]:
    """
    A dedicated helper for making cloud LLM API calls, similar to llm_utils._post_responses.
    Can be expanded to handle different providers.
    """
    # Define API endpoint based on provider
    url = ""
    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
    elif provider == "anthropic":
        # Example for Anthropic, adjust as needed
        url = "https://api.anthropic.com/v1/messages" 
        # Anthropic headers are different
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        # Anthropic payload structure might be different, e.g., "messages" instead of "input"
        # and "model" at top level
        anthropic_payload = {
            "model": payload["model"],
            "messages": [{"role": "user", "content": payload["input"][0]["content"] if payload["input"] else ""}],
            "max_tokens": payload.get("max_output_tokens", 1000),
            "temperature": payload.get("temperature", 0.7)
        }
        # This is a placeholder and needs full implementation for Anthropic
        # For now, we'll assume OpenAI compatible endpoint or handle specific providers
        # more thoroughly.
        # Fallback to OpenAI compatible for now if not fully implemented.
        print(f"\033[93mmartin: Warning - Anthropic provider not fully implemented. Attempting with OpenAI-compatible API.\033[0m")
        url = "https://api.openai.com/v1/chat/completions" # Fallback
        payload = anthropic_payload # Use adjusted payload
    else:
        return {"error": {"message": f"Unsupported cloud provider: {provider}"}}
    
    if not url:
        return {"error": {"message": f"No API URL defined for provider: {provider}"}}

    headers_to_use = HEADERS.copy()
    headers_to_use["Authorization"] = f"Bearer {api_key}" if provider == "openai" else headers.get("x-api-key") # Adjust auth
    
    last_err: Optional[Dict[str, Any]] = None
    # No tqdm for cloud calls currently to avoid polluting main CLI
    
    # Adapt payload for OpenAI Chat Completions API
    openai_payload_or_similar = {
        "model": payload["model"],
        "messages": payload["input"],
        "temperature": payload.get("temperature", 0.7),
        "max_tokens": payload.get("max_output_tokens", 500),
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            import requests
            r = requests.post(url, headers=headers_to_use, json=openai_payload_or_similar, timeout=timeout)
            status = r.status_code
            text = r.text or ""

            if status == 200:
                try:
                    data = r.json()
                except Exception as e:
                    last_err = {"message": "Invalid JSON from API", "detail": str(e), "body": text[:2000]}
                    break
                
                # Extract relevant part of OpenAI chat completions response
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    if "content" in message:
                        return {"output_text": message["content"]}
                # For Anthropic, check for data["content"][0]["text"]
                if "content" in data and len(data["content"]) > 0:
                     if data["content"][0].get("type") == "text":
                         return {"output_text": data["content"][0].get("text")}

                return {"output_text": ""} # Return empty if no content

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

        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))

    return {"error": last_err or {"message": "Unknown error"}}
