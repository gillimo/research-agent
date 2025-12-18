#!/usr/bin/env python
"""
Pipe-friendly CLI bridge for the two-agent flow.

Behavior:
- Reads a prompt from stdin or CLI args.
- Calls local model first (Ollama by default).
- Optionally calls a cloud CLI (Codex/Gemini/llm/etc.) via a template command.
- Prints provenance-tagged output.

Cloud command template:
- Set env CLOUD_CMD to something like:
  CLOUD_CMD='codex --model gpt-4o --prompt "{prompt}"'
  CLOUD_CMD='gemini --model gemini-1.5-pro "{prompt}"'
  CLOUD_CMD='llm -m gpt-4o "{prompt}"'
- The string "{prompt}" will be replaced with the sanitized prompt.
"""

import argparse
import os
import shlex
import subprocess
import sys
from typing import Optional, Tuple


def run_command(cmd: str, prompt: str) -> Tuple[str, str, int]:
    """Run a shell command with the prompt substituted."""
    expanded = cmd.replace("{prompt}", prompt)
    proc = subprocess.run(
        expanded,
        input=None,
        capture_output=True,
        text=True,
        shell=True,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def run_local(model: str, prompt: str) -> Tuple[str, str, int]:
    """Invoke the local model via Ollama."""
    cmd = ["ollama", "run", model, prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def sanitize(prompt: str) -> str:
    """Placeholder sanitization; extend with redaction/allowlist rules."""
    return prompt.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-agent CLI bridge")
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text (if empty, read from stdin)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read prompt from stdin",
    )
    parser.add_argument(
        "--local-model",
        default="phi3",
        help="Ollama model name (default: phi3)",
    )
    parser.add_argument(
        "--cloud-mode",
        choices=["off", "always"],
        default="off",
        help="Call cloud CLI after local (default: off)",
    )
    parser.add_argument(
        "--cloud-cmd",
        default=os.environ.get("CLOUD_CMD", ""),
        help='Cloud command template with {prompt} placeholder (env CLOUD_CMD honored)',
    )
    args = parser.parse_args()

    if args.stdin:
        prompt_text = sys.stdin.read().strip()
    else:
        prompt_text = " ".join(args.prompt).strip()

    if not prompt_text:
        print("error: no prompt provided (use args or --stdin)", file=sys.stderr)
        return 1

    sanitized = sanitize(prompt_text)

    local_out, local_err, local_code = run_local(args.local_model, sanitized)
    if local_err:
        print(f"[local:error] {local_err}", file=sys.stderr)

    print("=== local ===")
    print(local_out)

    if args.cloud_mode == "always":
        if not args.cloud_cmd:
            print("warning: cloud-mode=always but no CLOUD_CMD/--cloud-cmd provided; skipping cloud call", file=sys.stderr)
        else:
            cloud_out, cloud_err, cloud_code = run_command(args.cloud_cmd, sanitized)
            if cloud_err:
                print(f"[cloud:error] {cloud_err}", file=sys.stderr)
            print("=== cloud ===")
            print(cloud_out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
