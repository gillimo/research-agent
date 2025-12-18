import json
from typing import Optional

import requests


def run_ollama_chat(model: str, prompt: str, host: str = "http://localhost:11434") -> Optional[str]:
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
