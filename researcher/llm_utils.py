import os
import json
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List, Callable
from researcher.local_llm import run_ollama_chat
from researcher.config_loader import load_config

# Ensure .env is loaded before reading API key.
try:
    from researcher.config_loader import _load_env_file
    _load_env_file(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

# --- Constants (adapted from Martin) ---
RESPONSES_URL = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1/chat/completions")
API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
if not API_KEY:
    print("\033[93mmartin: Warning - OPENAI_API_KEY not set; API calls will fail.\033[0m")
_LOGGER = logging.getLogger("researcher.llm_utils")

TIMEOUT_S = 60
MAX_RETRIES = 3
BACKOFF_BASE_S = 0.75

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {API_KEY or ''}",
}

# Model selection (adapted from Martin - these will eventually be configured via researcher's config)
MODEL_MAIN = os.getenv("RESEARCHER_MODEL_MAIN", "gpt-5-mini") # Adapted for current OpenAI naming
MODEL_MINI = os.getenv("RESEARCHER_MODEL_MINI", "gpt-5-mini") # Adapted for current OpenAI naming
MODEL_FALLBACK = os.getenv("RESEARCHER_MODEL_FALLBACK", "gpt-4o-mini")

SHOW_API_BARS = False # Controlled by researcher's config/verbosity

# --- LLM API Helpers (adapted from Martin) ---
def _resolve_endpoint(base: str, path: str) -> str:
    if not base:
        return "https://api.openai.com/v1" + path
    clean = base.rstrip("/")
    if clean.endswith("/v1"):
        return clean + path
    if clean.endswith("/chat/completions"):
        return clean[:-len("/chat/completions")] + path
    if clean.endswith("/responses"):
        return clean
    return clean + "/v1" + path


def _post_responses(
    payload: Dict[str, Any],
    timeout: int = TIMEOUT_S,
    label: str = "API",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Makes an API call to the LLM endpoint (placeholder for now).
    Handles retries and basic error processing.
    """
    # TODO: This function will need to be properly integrated with researcher's
    # local LLM (Ollama) and cloud_bridge for actual API calls.
    # For now, it's a structural port from Martin.

    last_err: Optional[Dict[str, Any]] = None
    bar_ctx = None
    if SHOW_API_BARS:
        from tqdm import tqdm
        bar_ctx = tqdm(total=MAX_RETRIES, desc=label, unit="try", leave=False)

    # Adapt payload for OpenAI Chat Completions API
    max_out = payload.get("max_output_tokens", 500)
    model_name = str(payload.get("model", "") or "")
    use_responses_api = model_name.startswith("gpt-5")
    if use_responses_api:
        openai_payload = {
            "model": payload["model"],
            "input": payload.get("input", []),
            "max_output_tokens": max_out,
        }
    else:
        openai_payload = {
            "model": payload["model"],
            "messages": payload["input"],
            "temperature": payload.get("temperature", 0.7),
            "max_tokens": max_out,
        }
    endpoint = _resolve_endpoint(RESPONSES_URL, "/responses" if use_responses_api else "/chat/completions")
    tried_local_fallback = False

    def _chat_fallback() -> Dict[str, Any]:
        chat_payload = {
            "model": MODEL_FALLBACK,
            "messages": payload.get("input", []),
        }
        chat_endpoint = _resolve_endpoint(RESPONSES_URL, "/chat/completions")
        try:
            import requests
            r = requests.post(chat_endpoint, headers=HEADERS, json=chat_payload, timeout=timeout)
            if r.status_code != 200:
                try:
                    j = r.json()
                except Exception:
                    j = {}
                api_err = j.get("error")
                if isinstance(api_err, dict):
                    return {"error": {"message": api_err.get("message") or f"HTTP {r.status_code}"}}
                return {"error": {"message": f"HTTP {r.status_code}", "body": (r.text or "")[:2000]}}
            data = r.json()
            if "choices" in data and len(data["choices"]) > 0:
                message = data["choices"][0].get("message", {})
                if "content" in message:
                    return {"output_text": message["content"]}
            return {"error": {"message": "empty_response", "detail": "chat fallback returned no content"}}
        except Exception as exc:
            return {"error": {"message": "chat_fallback_failed", "detail": str(exc)}}

    if progress_cb:
        progress_cb("contacting model")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if progress_cb:
                progress_cb(f"request {attempt}/{MAX_RETRIES}")
            import requests
            r = requests.post(endpoint, headers=HEADERS, json=openai_payload, timeout=timeout)
            status = r.status_code
            text = r.text or ""

            if status == 200:
                try:
                    data = r.json()
                except Exception as e:
                    last_err = {"message": "Invalid JSON from API", "detail": str(e), "body": text[:2000]}
                    if bar_ctx: bar_ctx.update(1)
                    break
                if bar_ctx:
                    bar_ctx.update(MAX_RETRIES - bar_ctx.n); bar_ctx.close()
                
                if data.get("error") is not None:
                    api_err = data.get("error")
                    if isinstance(api_err, dict):
                        return {"error": {"message": api_err.get("message") or "API error", "type": api_err.get("type"), "code": api_err.get("code")}}
                    return {"error": {"message": str(api_err) or "API error"}}
                # Extract relevant part of OpenAI response.
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    if "content" in message:
                        return {"output_text": message["content"]}
                if isinstance(data.get("output_text"), str):
                    return {"output_text": data.get("output_text") or ""}
                output = data.get("output")
                if isinstance(output, list):
                    for item in output:
                        if not isinstance(item, dict):
                            continue
                        if isinstance(item.get("output_text"), str) and item.get("output_text"):
                            return {"output_text": item.get("output_text") or ""}
                        if item.get("type") not in (None, "message", "output_text", "text"):
                            continue
                        if isinstance(item.get("text"), str) and item.get("text"):
                            return {"output_text": item.get("text") or ""}
                        content = item.get("content", [])
                        if isinstance(content, str) and content:
                            return {"output_text": content}
                        if not isinstance(content, list):
                            continue
                        for c in content:
                            if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                                txt = c.get("text") or ""
                                if txt:
                                    return {"output_text": txt}
                if use_responses_api:
                    return _chat_fallback()
                return {"error": {"message": "empty_response", "detail": "no output_text in response payload"}}

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

                # If we hit quota/billing limits, try to fall back to the local model once.
                if _is_quota_error(last_err) and not tried_local_fallback:
                    tried_local_fallback = True
                    local_resp = _local_fallback_answer(payload)
                    if local_resp.get("output_text"):
                        if bar_ctx:
                            bar_ctx.close()
                        return local_resp
        except requests.RequestException as e:
            last_err = {"message": "Network error", "detail": str(e)}

        if bar_ctx: bar_ctx.update(1)
        if attempt < MAX_RETRIES:
            if progress_cb:
                delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
                progress_cb(f"retrying in {delay:.1f}s")
            time.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))

    if bar_ctx: bar_ctx.close()

    if _is_quota_error(last_err) and not tried_local_fallback:
        local_resp = _local_fallback_answer(payload)
        if local_resp.get("output_text"):
            return local_resp

    return {"error": last_err or {"message": "Unknown error"}}


def _is_quota_error(err: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(err, dict):
        return False
    msg = (err.get("message") or "").lower()
    code = (err.get("code") or "").lower()
    http_status = err.get("http_status")
    return (
        "quota" in msg
        or "billing" in msg
        or "credit" in msg
        or code in {"insufficient_quota", "quota_exceeded"}
        or http_status == 429
    )


def _local_fallback_answer(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-effort local LLM fallback using Ollama when cloud quota is hit.
    Returns {"output_text": "..."} on success, else {}.
    """
    try:
        cfg = load_config()
    except Exception:
        return {}
    model = cfg.get("local_model", "phi3")
    host = cfg.get("ollama_host", "http://localhost:11434")
    msgs = payload.get("input") or []
    prompt_parts: List[str] = []
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("content"):
                prompt_parts.append(str(m.get("content")))
    prompt = "\n\n".join(prompt_parts).strip()
    if not prompt:
        return {}
    text = run_ollama_chat(model, prompt, host)
    if text:
        return {"output_text": text, "local_model": model}
    return {}

def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    """
    Extracts the main output text from the LLM response JSON.
    Adapted for OpenAI chat completions format.
    """
    if not isinstance(resp_json, dict):
        return ""
    err = resp_json.get("error", None)
    if err is not None:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        print(f"\033[93mmartin: LLM error: {msg}\033[0m")
        return ""
    
    # In _post_responses, I'm already returning {"output_text": content}, so extract directly
    output_text = resp_json.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    # Fallback to original Martin logic if "output_text" is not directly set (less likely with new _post_responses)
    out = []
    try:
        for item in resp_json.get("output", []): # This structure might be specific to Martin's API
            if isinstance(item, dict) and item.get("type") == "message":
                for c in item.get("content", []):
                    if isinstance(c, dict):
                        if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                            out.append(c["text"])
                        elif "text" in c and isinstance(c["text"], str):
                            out.append(c["text"])
    except Exception:
        pass
    return "\n".join([s for s in out if s]).strip()

# --- Summarizers / Diagnosis / Rephraser (adapted from Martin) ---
# NOTE: interaction_history and current_username are global variables in Martin's original file.
# These will need to be passed in or managed in a different way in researcher.
# For now, using placeholders or assuming context will be provided.
interaction_history: List[str] = [] # Placeholder
current_username = os.getenv("USER") or "user" # Placeholder

def summarize_progress(text: str) -> str:
    """Summarizes recent CLI output in 2-3 short bullet points."""
    prompt = (
        "You are a concise AI assistant. Summarize the recent CLI output in 2-3 short bullet points. "
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
    """Diagnoses a failed command and suggests fix steps."""
    prompt = (
        "You are an AI assistant. Analyze the failed command and output. Provide a brief diagnosis (1-3 sentences) "
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
    """Rephrases text succinctly and politely."""
    intro = (
        "THE REST OF THE FOLLOWING MESSAGE IS PURELY FOR CONTEXT. IT IS NOT FROM THE USER: "
        "You are a friendly, professional AI assistant. "
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

def get_thinking_gpt_response(prompt: str, error_message: str) -> str:
    """General-purpose LLM call for reasoning and generating responses."""
    intro_message = (
        "THE REST OF THE FOLLOWING MESSAGE IS PURELY FOR CONTEXT. IT IS NOT FROM THE USER: "
        "You are a friendly, professional AI assistant. "
        "Whenever suggesting a terminal command to execute, precede it with 'command:' "
        "and append any flags needed to run non-interactively."
    )
    background_knowledge = (
        f"DIRECTIVE: Provide current terminal commands when applicable. "
        f"Each command must start with 'command: '. Username is '{current_username}'."
    )
    # Using a placeholder for interaction_history
    recent = "\n".join(interaction_history[-5:]) if interaction_history else ""

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
        print(f"\033[93mmartin: Empty response; check logs or try again.\033[0m")
    return out
