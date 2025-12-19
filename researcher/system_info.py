# researcher/system_info.py
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from researcher.state_manager import load_state, ROOT_DIR, DEFAULT_STATE
from researcher.llm_utils import current_username


def system_snapshot() -> Dict[str, Any]:
    """Gathers a snapshot of the current system environment."""
    st = load_state()
    ws_path = (ROOT_DIR / (st.get("workspace", {}).get("path") or "workspace")).resolve()
    ws_path.mkdir(parents=True, exist_ok=True)

    bins = ["python3", "pip3", "git", "node", "npm", "java", "javac", "make", "ollama"]
    path_map = {b: shutil.which(b) for b in bins}
    platform_info = st.get("platform", DEFAULT_STATE["platform"])

    return {
        "platform": platform_info,
        "workspace": str(ws_path),
        "binaries": path_map,
        "has_api_key": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "username": current_username,
    }
