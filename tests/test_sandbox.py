import os
from pathlib import Path

from researcher.runner import enforce_sandbox


def test_read_only_blocks_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    allowed, reason = enforce_sandbox("echo hi > file.txt", "read-only", str(tmp_path))
    assert allowed is False
    assert "read-only" in reason


def test_workspace_write_allows_relative_when_in_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    allowed, _reason = enforce_sandbox("mkdir newdir", "workspace-write", str(tmp_path))
    assert allowed is True


def test_workspace_write_blocks_outside_workspace(tmp_path, monkeypatch):
    outside = tmp_path.parent
    monkeypatch.chdir(outside)
    allowed, _reason = enforce_sandbox("mkdir newdir", "workspace-write", str(tmp_path))
    assert allowed is False


def test_workspace_write_blocks_redirect_outside(tmp_path, monkeypatch):
    outside = tmp_path.parent
    monkeypatch.chdir(outside)
    allowed, _reason = enforce_sandbox("echo hi > out.txt", "workspace-write", str(tmp_path))
    assert allowed is False


def test_workspace_write_blocks_git_write(tmp_path, monkeypatch):
    outside = tmp_path.parent
    monkeypatch.chdir(outside)
    allowed, _reason = enforce_sandbox("git commit -m \"x\"", "workspace-write", str(tmp_path))
    assert allowed is False
