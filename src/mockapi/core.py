"""Core: route definitions, config loading, matching, and templating.

A routes file is JSON with a top-level object::

    {
      "routes": [
        {"method": "GET", "path": "/users/{id}", "status": 200,
         "body": {"id": "{{id}}", "name": "Ada Lovelace"}},
        {"method": "POST", "path": "/users", "status": 201,
         "body": {"id": "{{uuid}}", "name": "{{body.name}}", "created": "{{now}}"}}
      ]
    }

``body`` may be any JSON value (object, list, string, number) or the
name of a file to serve verbatim (``"file": "response.json"`` instead).
Template placeholders ``{{name}}`` inside string values are replaced
from, in order of precedence: path params, query params, JSON request
body fields (``{{body.name}}``), plus ``{{now}}`` (ISO timestamp) and
``{{uuid}}`` (a fresh UUID4).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


class ConfigError(ValueError):
    """Raised when a routes file is malformed."""


@dataclass
class Route:
    method: str
    path: str
    status: int = 200
    body: Any = None
    headers: dict = field(default_factory=dict)
    delay_ms: int = 0
    # Path segments, e.g. ["users", "{id}"] for "/users/{id}".
    segments: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.segments = [s for s in self.path.split("/") if s != ""]

    def match(self, method: str, path: str) -> Optional[dict]:
        """Return captured path params if this route matches, else None."""
        if method.upper() != self.method:
            return None
        parts = [s for s in path.split("/") if s != ""]
        if len(parts) != len(self.segments):
            return None
        params: dict[str, str] = {}
        for want, got in zip(self.segments, parts):
            if want.startswith("{") and want.endswith("}"):
                params[want[1:-1]] = got
            elif want != got:
                return None
        return params

    def path_matches(self, path: str) -> bool:
        """True if the path shape matches regardless of method."""
        parts = [s for s in path.split("/") if s != ""]
        if len(parts) != len(self.segments):
            return False
        return all(
            (want.startswith("{") and want.endswith("}")) or want == got
            for want, got in zip(self.segments, parts)
        )


def _validate_route(raw: Any, index: int) -> Route:
    where = f"routes[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each route must be an object")
    method = str(raw.get("method", "GET")).upper()
    if method not in VALID_METHODS:
        raise ConfigError(f"{where}: unknown method {method!r}")
    path = raw.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigError(f"{where}: 'path' must be a string starting with '/'")
    status = raw.get("status", 200)
    if not isinstance(status, int) or not 100 <= status <= 599:
        raise ConfigError(f"{where}: 'status' must be an integer 100-599")
    if "body" in raw and "file" in raw:
        raise ConfigError(f"{where}: use either 'body' or 'file', not both")
    if "file" in raw and not isinstance(raw["file"], str):
        raise ConfigError(f"{where}: 'file' must be a string path")
    headers = raw.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise ConfigError(f"{where}: 'headers' must be an object of strings")
    delay = raw.get("delay_ms", 0)
    if not isinstance(delay, int) or delay < 0:
        raise ConfigError(f"{where}: 'delay_ms' must be a non-negative integer")
    return Route(
        method=method,
        path=path,
        status=status,
        body=raw.get("body"),
        headers=dict(headers),
        delay_ms=delay,
    )


def load_routes(path: str | Path, base_dir: Optional[str | Path] = None) -> list[Route]:
    """Load and validate a routes JSON file.

    ``file`` bodies are resolved relative to ``base_dir`` (default: the
    directory containing the routes file) and inlined at load time.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"routes file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"routes file is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("routes"), list):
        raise ConfigError("routes file must be a JSON object with a 'routes' list")
    base = Path(base_dir) if base_dir else path.parent
    routes: list[Route] = []
    for i, raw in enumerate(data["routes"]):
        route = _validate_route(raw, i)
        if isinstance(raw, dict) and "file" in raw:
            file_path = base / raw["file"]
            try:
                route.body = file_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                raise ConfigError(f"routes[{i}]: file not found: {file_path}")
            route.headers.setdefault("Content-Type", _guess_content_type(file_path))
        routes.append(route)
    return routes


def _guess_content_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
        ".xml": "application/xml",
        ".txt": "text/plain; charset=utf-8",
    }.get(path.suffix.lower(), "application/octet-stream")


def match_route(routes: list[Route], method: str, path: str):
    """Find the first matching route.

    Returns ``(route, params)`` or ``(None, None)``. If the path matches
    a route under a different method, the caller can use
    :func:`path_known` to answer 405 instead of 404.
    """
    for route in routes:
        params = route.match(method, path)
        if params is not None:
            return route, params
    return None, None


def path_known(routes: list[Route], path: str) -> bool:
    """True if any route matches this path shape under any method."""
    return any(route.path_matches(path) for route in routes)


def _lookup(name: str, context: dict) -> Optional[str]:
    """Resolve one placeholder name against the template context."""
    if name in context:
        value = context[name]
        return "" if value is None else str(value)
    if "." in name:
        head, rest = name.split(".", 1)
        nested = context.get(head)
        if isinstance(nested, dict):
            value = nested.get(rest)
            return "" if value is None else str(value)
    return None


def render_body(body: Any, context: dict) -> Any:
    """Recursively substitute ``{{placeholders}}`` in a body value.

    Unknown placeholders are left untouched so a typo is visible in the
    response instead of silently vanishing.
    """
    if isinstance(body, str):
        def repl(match: re.Match) -> str:
            value = _lookup(match.group(1), context)
            return match.group(0) if value is None else value

        return _PLACEHOLDER.sub(repl, body)
    if isinstance(body, dict):
        return {key: render_body(value, context) for key, value in body.items()}
    if isinstance(body, list):
        return [render_body(item, context) for item in body]
    return body


def build_context(
    params: dict, query: dict, request_body: Any
) -> dict:
    """Assemble the template context for one request."""
    context: dict[str, Any] = {}
    if isinstance(request_body, dict):
        context["body"] = request_body
    context.update(query)
    context.update(params)
    context["now"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    context["uuid"] = str(uuid.uuid4())
    return context
