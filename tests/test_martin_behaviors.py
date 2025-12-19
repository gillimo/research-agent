import os
import shlex
from researcher.command_utils import extract_commands


def test_extract_commands_handles_cd_and_commands():
    text = """command: cd /tmp
command: ls -l
command: echo hi"""
    cmds = extract_commands(text)
    
    expected_path = os.path.abspath('/tmp')
    
    expected_cmds = [
        "cd /tmp",
        f"cd {shlex.quote(expected_path)} && ls -l",
        f"cd {shlex.quote(expected_path)} && echo hi"
    ]
    assert cmds == expected_cmds
