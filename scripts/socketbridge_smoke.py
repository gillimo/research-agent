#!/usr/bin/env python
"""
Smoke test for SocketBridge-backed IPC.

Usage:
  python scripts/socketbridge_smoke.py --host 127.0.0.1 --port 6001 --token <token>
"""
import argparse
import os
import sys

from socketbridge.client import send


def main() -> int:
    parser = argparse.ArgumentParser(description="SocketBridge smoke test for Researcher IPC")
    parser.add_argument("--host", default=os.environ.get("LIBRARIAN_IPC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LIBRARIAN_IPC_PORT", "6001")))
    parser.add_argument("--token", default=os.environ.get("LIBRARIAN_IPC_TOKEN", ""))
    parser.add_argument("--type", default="smoke")
    parser.add_argument("--text", default="socketbridge smoke")
    args = parser.parse_args()

    payload = {
        "type": args.type,
        "details": {"text": args.text},
    }
    try:
        resp = send(args.host, args.port, payload, token=args.token or None, timeout=5.0)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
