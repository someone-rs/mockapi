"""HTTP server: turns routes into a live fake API."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import Route, build_context, match_route, path_known, render_body

__all__ = ["make_server", "serve"]


def _flatten_query(query: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[0] for key, values in query.items() if values}


class _Handler(BaseHTTPRequestHandler):
    routes: list[Route] = []
    latency_ms: int = 0
    quiet: bool = False
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------
    def log_message(self, *args):  # silence BaseHTTPRequestHandler's stderr log
        pass

    def _log(self, method: str, path: str, status: int, elapsed_ms: float) -> None:
        if not self.quiet:
            print(
                f"{method} {path} -> {status} ({elapsed_ms:.0f}ms)",
                file=sys.stderr,
                flush=True,
            )

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- request handling ----------------------------------------------
    def _handle(self, method: str) -> None:
        started = time.monotonic()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = _flatten_query(parse_qs(parsed.query))

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        request_body = None
        if raw:
            try:
                request_body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                request_body = raw.decode("utf-8", errors="replace")

        route, params = match_route(self.routes, method, path)
        if route is None:
            status = 405 if path_known(self.routes, path) else 404
            message = (
                "method not allowed" if status == 405 else "route not found"
            )
            self._send_json(status, {"error": message, "path": path})
            self._log(method, path, status, (time.monotonic() - started) * 1000)
            return

        delay = route.delay_ms + self.latency_ms
        if delay:
            time.sleep(delay / 1000)

        context = build_context(params or {}, query, request_body)
        body = render_body(route.body, context)

        if isinstance(body, (dict, list)):
            payload = json.dumps(body).encode("utf-8")
            content_type = "application/json"
        elif isinstance(body, str):
            payload = body.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        elif body is None:
            payload = b""
            content_type = "text/plain; charset=utf-8"
        else:
            payload = str(body).encode("utf-8")
            content_type = "text/plain; charset=utf-8"

        headers = {"Content-Type": content_type}
        headers.update(route.headers)
        self.send_response(route.status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)
        self._log(method, path, route.status, (time.monotonic() - started) * 1000)

    do_GET = lambda self: self._handle("GET")
    do_POST = lambda self: self._handle("POST")
    do_PUT = lambda self: self._handle("PUT")
    do_PATCH = lambda self: self._handle("PATCH")
    do_DELETE = lambda self: self._handle("DELETE")
    do_HEAD = lambda self: self._handle("HEAD")
    do_OPTIONS = lambda self: self._handle("OPTIONS")


def make_server(
    routes: list[Route],
    host: str = "127.0.0.1",
    port: int = 8000,
    latency_ms: int = 0,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    """Build (but do not start) a mock API server bound to ``host:port``."""
    handler = type(
        "MockApiHandler",
        (_Handler,),
        {"routes": routes, "latency_ms": latency_ms, "quiet": quiet},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(
    routes: list[Route],
    host: str = "127.0.0.1",
    port: int = 8000,
    latency_ms: int = 0,
    quiet: bool = False,
) -> None:
    """Serve forever (until Ctrl-C)."""
    server = make_server(routes, host, port, latency_ms, quiet)
    actual_port = server.server_address[1]
    if not quiet:
        print(
            f"mockapi serving {len(routes)} route(s) on http://{host}:{actual_port}",
            file=sys.stderr,
        )
        print("Press Ctrl-C to stop.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nmockapi stopped.", file=sys.stderr)
    finally:
        server.server_close()


def serve_in_thread(routes, **kwargs) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start a server on a background thread; returns (server, thread).

    Handy for tests and for embedding mockapi in other tools.
    """
    server = make_server(routes, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
