import os
import platform
from pathlib import Path
from typing import Dict, Any, List


def _list_drives_windows() -> List[str]:
    drives = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        path = f"{c}:\\"
        if os.path.exists(path):
            drives.append(path)
    return drives


def get_system_context() -> Dict[str, Any]:
    home = Path.home()
    cwd = Path.cwd()
    userprofile = os.environ.get("USERPROFILE", "")
    onedrive = os.environ.get("OneDrive", "")
    desktop = Path(userprofile) / "Desktop" if userprofile else home / "Desktop"
    onedrive_desktop = Path(onedrive) / "Desktop" if onedrive else None

    ctx: Dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "paths": {
            "cwd": str(cwd),
            "home": str(home),
            "userprofile": userprofile or str(home),
            "desktop": str(desktop),
            "onedrive_desktop": str(onedrive_desktop) if onedrive_desktop else "",
        },
        "env": {
            "username": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "shell": os.environ.get("SHELL") or os.environ.get("ComSpec") or "",
        },
        "drives": _list_drives_windows() if os.name == "nt" else ["/"],
    }
    return ctx
