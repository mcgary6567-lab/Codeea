"""Start the development server.

    python run.py                 # dual-stack: works on http://localhost:8000 and http://127.0.0.1:8000
    python run.py --reload        # auto-reload on code changes (IPv4 loopback only)
    python run.py --port 9000
    python run.py --host 127.0.0.1    # IPv4 loopback only (not reachable as "localhost" on some Windows setups)

Why a custom socket: on Windows "localhost" usually resolves to the IPv6 address ::1, while a server
bound to 127.0.0.1 only answers IPv4. A browser then connects to ::1, gets nothing, and appears to hang.
Binding an AF_INET6 socket with IPV6_V6ONLY disabled accepts IPv4 and IPv6 on the same port, so both
http://localhost:8000 and http://127.0.0.1:8000 work.
"""
from __future__ import annotations

import argparse
import socket
import sys

import uvicorn

from app.config import settings

DUAL_STACK = "dual"


def dual_stack_socket(port: int, backlog: int = 2048) -> socket.socket | None:
    """An IPv6 socket that also accepts IPv4 (v4-mapped) connections. None if unsupported."""
    if not socket.has_ipv6:
        return None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 0 = NOT v6-only, i.e. accept IPv4 too. Windows defaults this to 1.
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
        sock.listen(backlog)
        sock.set_inheritable(True)
        return sock
    except OSError as exc:
        print(f"Dual-stack bind unavailable ({exc}); falling back to {settings.HOST}.", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DUAL_STACK,
                        help='Bind address. Default "dual" listens on IPv4 and IPv6 so "localhost" works.')
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    banner = (
        "\n" + "=" * 62 +
        "\n  Online Quran College - Digital Operating System" +
        f"\n  http://localhost:{args.port}   or   http://127.0.0.1:{args.port}" +
        "\n  Sign in: admin@oqc.local  /  Admin@12345" +
        "\n" + "=" * 62 + "\n"
    )

    # --reload needs uvicorn to own the socket, so it cannot use a pre-bound one.
    if args.reload or args.host != DUAL_STACK:
        host = settings.HOST if args.host == DUAL_STACK else args.host
        print(banner)
        uvicorn.run("app.main:app", host=host, port=args.port, reload=args.reload,
                    workers=1 if args.reload else args.workers)
        return

    sock = dual_stack_socket(args.port)
    if sock is None:
        print(banner)
        uvicorn.run("app.main:app", host=settings.HOST, port=args.port, workers=args.workers)
        return

    print(banner)
    config = uvicorn.Config("app.main:app", workers=args.workers)
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
