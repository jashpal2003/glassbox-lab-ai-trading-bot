#!/usr/bin/env python
"""
run.py - Production entrypoint for the GlassBox Options dashboard.

Why this exists rather than a bare `uvicorn web.app:app --port 8001`:

  * Port conflicts should not derail a live demo. Other software on the machine (Docker Desktop
    is a common culprit) can hold the default port. This picks the next free port instead of
    dying, and prints the URL it actually bound to.
  * The port comes from the PORT environment variable / .env, so deployment targets that inject
    PORT work without changing the start command.

Usage:
    python run.py                 # PORT from .env, else 8001, else next free port
    PORT=9000 python run.py       # explicit port
    python run.py --strict        # fail loudly instead of shifting to a free port
"""

import os
import socket
import sys

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PORT = 8001
MAX_PORT_PROBES = 20


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(host: str, preferred: int, strict: bool) -> int:
    if port_is_free(host, preferred):
        return preferred
    if strict:
        print(f"ERROR: port {preferred} is already in use and --strict was given.", file=sys.stderr)
        sys.exit(1)
    for candidate in range(preferred + 1, preferred + MAX_PORT_PROBES):
        if port_is_free(host, candidate):
            print(f"NOTE: port {preferred} is in use; starting on {candidate} instead.")
            return candidate
    print(f"ERROR: no free port found in {preferred}-{preferred + MAX_PORT_PROBES}.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    import uvicorn

    strict = "--strict" in sys.argv
    host = os.getenv("HOST", "127.0.0.1")
    # Bind probing always uses a concrete interface; 0.0.0.0 probes as localhost.
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host

    try:
        preferred = int(os.getenv("PORT", DEFAULT_PORT))
    except ValueError:
        preferred = DEFAULT_PORT

    port = resolve_port(probe_host, preferred, strict)

    print("=" * 62)
    print("  GlassBox Options - Verifiable AI Options Trading Agent")
    print(f"  Dashboard:  http://{probe_host}:{port}")
    print(f"  API docs:   http://{probe_host}:{port}/docs")
    print("=" * 62)

    uvicorn.run("web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
