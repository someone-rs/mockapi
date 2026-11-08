"""Command line interface for mockapi."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import ConfigError, load_routes
from .server import serve

EXAMPLE_ROUTES = """{
  "routes": [
    {
      "method": "GET",
      "path": "/health",
      "body": {"status": "ok"}
    },
    {
      "method": "GET",
      "path": "/users",
      "body": [
        {"id": 1, "name": "Ada Lovelace"},
        {"id": 2, "name": "Alan Turing"}
      ]
    },
    {
      "method": "GET",
      "path": "/users/{id}",
      "body": {"id": "{{id}}", "name": "User {{id}}", "requested_at": "{{now}}"}
    },
    {
      "method": "GET",
      "path": "/search",
      "body": {"query": "{{q}}", "results": []}
    },
    {
      "method": "POST",
      "path": "/users",
      "status": 201,
      "body": {"id": "{{uuid}}", "name": "{{body.name}}", "created": "{{now}}"}
    },
    {
      "method": "DELETE",
      "path": "/users/{id}",
      "status": 204
    }
  ]
}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mockapi",
        description="Fake REST API server from a JSON routes file.",
    )
    parser.add_argument(
        "routes",
        nargs="?",
        help="path to a routes JSON file (not needed with --init)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=0,
        help="extra delay added to every response, in ms (default: 0)",
    )
    parser.add_argument(
        "--init",
        nargs="?",
        const="routes.json",
        metavar="PATH",
        help="write an example routes file (default: routes.json) and exit",
    )
    parser.add_argument(
        "--print-routes",
        action="store_true",
        help="list the routes from the file and exit (does not start a server)",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="suppress request logs")
    parser.add_argument("--version", action="version", version=f"mockapi {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.init:
        target = Path(args.init)
        if target.exists():
            print(f"error: {target} already exists, refusing to overwrite", file=sys.stderr)
            return 2
        target.write_text(EXAMPLE_ROUTES, encoding="utf-8")
        print(f"wrote example routes to {target}")
        print(f"start it with:  mockapi {target}")
        return 0

    if not args.routes:
        print("error: a routes file is required (or use --init to create one)", file=sys.stderr)
        return 2
    if args.port < 1 or args.port > 65535:
        print("error: --port must be 1-65535", file=sys.stderr)
        return 2
    if args.latency_ms < 0:
        print("error: --latency-ms must be >= 0", file=sys.stderr)
        return 2

    try:
        routes = load_routes(args.routes)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_routes:
        print(f"{len(routes)} route(s) in {args.routes}:")
        for route in routes:
            delay = f"  [{route.delay_ms}ms]" if route.delay_ms else ""
            print(f"  {route.method:7} {route.path}  -> {route.status}{delay}")
        return 0

    try:
        serve(routes, host=args.host, port=args.port, latency_ms=args.latency_ms, quiet=args.quiet)
    except OSError as exc:
        print(f"error: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
