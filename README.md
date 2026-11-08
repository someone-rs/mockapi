# mockapi

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Fake REST API server from a JSON file — one command, zero backend.**

Building a frontend but the backend isn't ready? Testing a client against
endpoints that don't exist yet? Write your API as a JSON file, run one
command, and you have a real HTTP server answering on `localhost` — with
path params, query params, request-body echoing, custom statuses, delays,
and `{{uuid}}` / `{{now}}` templating built in.

No database, no framework, no config language to learn. Just JSON in,
HTTP out.

## Install

```bash
pip install mockapi
```

Requires Python 3.9+. Zero dependencies — standard library only.

## Quickstart

```bash
# 1. Generate an example routes file
mockapi --init

# 2. Start the server
mockapi routes.json
# mockapi serving 6 route(s) on http://127.0.0.1:8000
```

That's it. Your fake API is live.

## The routes file

A routes file is JSON with a `routes` list. Each route is a method, a
path (with `{params}`), a status, and a body:

```json
{
  "routes": [
    { "method": "GET", "path": "/health",
      "body": { "status": "ok" } },

    { "method": "GET", "path": "/users/{id}",
      "body": { "id": "{{id}}", "name": "User {{id}}",
                "requested_at": "{{now}}" } },

    { "method": "POST", "path": "/users", "status": 201,
      "body": { "id": "{{uuid}}", "name": "{{body.name}}",
                "created": "{{now}}" } },

    { "method": "DELETE", "path": "/users/{id}", "status": 204 }
  ]
}
```

Run it and hit it:

```bash
$ mockapi demo/routes.json --port 8000
mockapi serving 6 route(s) on http://127.0.0.1:8000

$ curl localhost:8000/users/42
{"id": "42", "name": "User 42", "requested_at": "2026-11-08T03:54:10Z"}

$ curl -X POST -H 'Content-Type: application/json' \
    -d '{"name": "Grace Hopper"}' localhost:8000/users
{"id": "8dc375c2-4389-4336-8350-4a4ce6e0e01a",
 "name": "Grace Hopper", "created": "2026-11-08T03:54:10Z"}

$ curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/nope
404
```

## Templating

Placeholders inside string bodies are replaced per request:

| Placeholder | Comes from |
|---|---|
| `{{id}}`, `{{slug}}`, ... | Path params (`/users/{id}`) |
| `{{q}}`, `{{page}}`, ... | Query string (`/search?q=cats`) |
| `{{body.name}}`, `{{body.items.0}}` | JSON request body (POST/PUT/PATCH) |
| `{{now}}` | Current UTC timestamp (ISO 8601) |
| `{{uuid}}` | A fresh UUID4 per request |

Path params beat query params on a name collision. Unknown
placeholders are left untouched, so a typo shows up in the response
instead of silently vanishing.

## Route options

```json
{
  "method": "GET",
  "path": "/slow-report",
  "status": 200,
  "delay_ms": 500,
  "headers": { "X-Rate-Limit": "100" },
  "body": { "...": "..." }
}
```

- `status` — any HTTP status, 100–599 (default `200`).
- `body` — any JSON value: object, list, string, number, or `null`
  (objects/lists are served as `application/json`, strings as
  `text/plain`). Omit it for an empty body (handy with `204`).
- `file` — serve a file verbatim instead of `body`
  (`"file": "response.json"`, resolved relative to the routes file;
  content type guessed from the extension).
- `headers` — extra response headers.
- `delay_ms` — per-route artificial latency, for testing spinners and
  timeouts.

Unmatched paths return `404 {"error": "route not found"}`; a path that
exists under another method returns `405 {"error": "method not allowed"}`.

## CLI

```
mockapi routes.json [--host 127.0.0.1] [--port 8000]
                    [--latency-ms 200] [--quiet]
mockapi --init [PATH]          # write an example routes file
mockapi routes.json --print-routes
mockapi --version
```

- `--latency-ms` adds the same delay to *every* response — simulate a
  slow backend in one flag.
- Request logs go to stderr (`GET /users/42 -> 200 (3ms)`); `--quiet`
  silences them.
- Bind `--host 0.0.0.0` to expose the mock on your LAN (e.g. for a phone
  or a colleague's machine).

## Use it as a library

```python
from mockapi import load_routes
from mockapi.server import serve_in_thread

routes = load_routes("routes.json")
server, _thread = serve_in_thread(routes, port=0)  # port 0 = any free port
port = server.server_address[1]
# ... run your integration tests against http://127.0.0.1:port ...
server.shutdown()
```

## Why mockapi?

- **Zero setup** — no Node, no Docker, no framework; one JSON file.
- **Honest errors** — config problems (bad method, bad status, missing
  file) fail fast with a plain-language message and exit code 2, before
  the server starts.
- **Real HTTP** — it's a genuine server, so `fetch`, `curl`, Postman,
  and your test suite all work against it unchanged.
- **Deterministic where it matters** — same routes file, same responses;
  only `{{now}}` and `{{uuid}}` vary, and only where you ask for them.

## Development

```bash
git clone https://github.com/someone-rs/mockapi
cd mockapi
pip install -e .[dev]   # or: pip install -e . && pip install pytest
pytest -q                # 26 tests, incl. live-server end-to-end tests
```

## License

MIT — see [LICENSE](LICENSE).
