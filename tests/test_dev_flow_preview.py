import os
from pathlib import Path

from researcher.dev_flow import _preview_and_confirm


def test_preview_and_confirm_auto_apply(tmp_path: Path):
    path = tmp_path / "sample.py"
    before = "print('a')\n"
    after = "print('b')\n"
    os.environ["MARTIN_AUTO_APPLY"] = "1"
    try:
        assert _preview_and_confirm(path, before, after) is True
    finally:
        os.environ.pop("MARTIN_AUTO_APPLY", None)
