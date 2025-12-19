import os
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

# Ensure .env is loaded before reading API key.
try:
    from researcher.config_loader import _load_env_file
    _load_env_file(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

# --- Constants (adapted from Martin) ---
RESPONSES_URL = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com/v1/chat/completions") # Changed to chat completions endpoint
API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
if not API_KEY:
    print("\033[93mmartin: Warning - OPENAI_API_KEY not set; API calls will fail.\033[0m")

TIMEOUT_S = 60
MAX_RETRIES = 3
BACKOFF_BASE_S = 0.75

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Authorization": f"Bearer {API_KEY or ''}",
}

# Model selection (adapted from Martin - these will eventually be configured via researcher's config)
MODEL_MAIN = os.getenv("RESEARCHER_MODEL_MAIN", "gpt-4o-mini") # Adapted for current OpenAI naming
MODEL_MINI = os.getenv("RESEARCHER_MODEL_MINI", "gpt-4o-mini") # Adapted for current OpenAI naming

SHOW_API_BARS = True # To be controlled by researcher's config/verbosity

# --- LLM API Helpers (adapted from Martin) ---
def _post_responses(payload: Dict[str, Any], timeout: int = TIMEOUT_S, label: str = "API") -> Dict[str, Any]:
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
    openai_payload = {
        "model": payload["model"],
        "messages": payload["input"],
        "temperature": payload.get("temperature", 0.7),
        "max_tokens": payload.get("max_output_tokens", 500),
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            import requests
            r = requests.post(RESPONSES_URL, headers=HEADERS, json=openai_payload, timeout=timeout)
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
                
                # Extract relevant part of OpenAI chat completions response
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    if "content" in message:
                        return {"output_text": message["content"]}
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

        if bar_ctx: bar_ctx.update(1)
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))

    if bar_ctx: bar_ctx.close()
    return {"error": last_err or {"message": "Unknown error"}}

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
