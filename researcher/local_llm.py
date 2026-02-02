import json
from typing import Optional, Callable, Dict, Any


def check_ollama_health(host: str = "http://localhost:11434", model: str = "") -> Dict[str, Any]:
    import requests
    url = f"{host.rstrip('/')}/api/tags"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return {"ok": False, "model": model, "status": resp.status_code}
        data = resp.json()
        models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
        return {"ok": True, "model": model, "available": models}
    except (requests.RequestException, json.JSONDecodeError):
        return {"ok": False, "model": model}


def run_ollama_chat(model: str, prompt: str, host: str = "http://localhost:11434") -> Optional[str]:
    import requests
    url = f"{host.rstrip('/')}/api/chat"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            return None
        # Newer Ollama streams; but standard chat returns json with "message"
        data = resp.json()
        if isinstance(data, dict):
            msg = data.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
        return None
    except (requests.RequestException, json.JSONDecodeError):
        return None


def run_ollama_chat_stream(
    model: str,
    prompt: str,
    host: str = "http://localhost:11434",
    on_token: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    import requests
    url = f"{host.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(url, json=payload, timeout=60, stream=True)
        if resp.status_code != 200:
            return None
        chunks: list[str] = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if data.get("done") is True:
                break
            msg = data.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and content:
                chunks.append(content)
                if on_token:
                    on_token(content)
        return "".join(chunks).strip() if chunks else None
    except (requests.RequestException, json.JSONDecodeError):
        return None
