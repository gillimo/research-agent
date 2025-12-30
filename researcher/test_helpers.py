from pathlib import Path
from typing import List


def suggest_test_commands(root: Path) -> List[str]:
    cmds: List[str] = []
    has_tests = (root / "tests").exists()
    has_pytest_cfg = (
        (root / "pyproject.toml").exists()
        or (root / "pytest.ini").exists()
        or (root / "tox.ini").exists()
        or (root / "setup.cfg").exists()
    )
    if has_tests:
        cmds.append("python -m pytest tests")
    if has_pytest_cfg:
        cmds.append("python -m pytest -q")
    if (root / "scripts").exists() and (root / "scripts" / "ingest_demo.py").exists():
        cmds.append("python scripts/ingest_demo.py --simple-index")
    return cmds
