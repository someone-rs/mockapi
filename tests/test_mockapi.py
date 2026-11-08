"""Tests for mockapi: config loading, matching, templating, live server."""

import json
import time
import urllib.error
import urllib.request

import pytest

from mockapi.core import (
    ConfigError,
    Route,
    build_context,
    load_routes,
    match_route,
    path_known,
    render_body,
)
from mockapi.server import serve_in_thread


def write_routes(tmp_path, data):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -- config loading -----------------------------------------------------

def test_load_routes_minimal(tmp_path):
    path = write_routes(tmp_path, {"routes": [{"method": "GET", "path": "/ping"}]})
    routes = load_routes(path)
    assert len(routes) == 1
    assert routes[0].method == "GET"
    assert routes[0].status == 200
    assert routes[0].body is None


def test_load_routes_rejects_bad_json(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_routes(path)


def test_load_routes_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_routes(tmp_path / "nope.json")


def test_load_routes_rejects_bad_method(tmp_path):
    path = write_routes(tmp_path, {"routes": [{"method": "FETCH", "path": "/x"}]})
    with pytest.raises(ConfigError, match="unknown method"):
        load_routes(path)


def test_load_routes_rejects_path_without_slash(tmp_path):
    path = write_routes(tmp_path, {"routes": [{"method": "GET", "path": "users"}]})
    with pytest.raises(ConfigError, match="starting with '/'"):
        load_routes(path)


def test_load_routes_rejects_bad_status(tmp_path):
    path = write_routes(tmp_path, {"routes": [{"method": "GET", "path": "/x", "status": 99}]})
    with pytest.raises(ConfigError, match="status"):
        load_routes(path)


def test_load_routes_rejects_body_and_file(tmp_path):
    path = write_routes(
        tmp_path,
        {"routes": [{"method": "GET", "path": "/x", "body": {}, "file": "x.json"}]},
    )
    with pytest.raises(ConfigError, match="either 'body' or 'file'"):
        load_routes(path)


def test_load_routes_file_body(tmp_path):
    (tmp_path / "hello.json").write_text('{"hello": "world"}', encoding="utf-8")
    path = write_routes(
        tmp_path, {"routes": [{"method": "GET", "path": "/x", "file": "hello.json"}]}
    )
    routes = load_routes(path)
    assert routes[0].body == '{"hello": "world"}'
    assert routes[0].headers["Content-Type"] == "application/json"


# -- matching ------------------------------------------------------------

def test_match_literal_and_param():
    routes = [
        Route("GET", "/users"),
        Route("GET", "/users/{id}"),
        Route("POST", "/users"),
    ]
    route, params = match_route(routes, "GET", "/users")
    assert route.path == "/users" and params == {}
    route, params = match_route(routes, "GET", "/users/42")
    assert route.path == "/users/{id}" and params == {"id": "42"}
    route, params = match_route(routes, "POST", "/users/42")
    assert route is None and params is None


def test_match_is_segment_exact():
    routes = [Route("GET", "/users/{id}")]
    assert match_route(routes, "GET", "/users/42/extra")[0] is None
    assert match_route(routes, "GET", "/users")[0] is None


def test_path_known_for_405():
    routes = [Route("GET", "/users/{id}")]
    assert path_known(routes, "/users/7")
    assert not path_known(routes, "/other")


def test_trailing_slash_tolerated_in_match():
    routes = [Route("GET", "/users/{id}")]
    assert match_route(routes, "GET", "/users/7/")[0] is not None


# -- templating ----------------------------------------------------------

def test_render_body_substitutes_params():
    context = build_context({"id": "42"}, {"q": "cats"}, {"name": "Ada"})
    rendered = render_body(
        {"id": "{{id}}", "q": "{{q}}", "name": "{{body.name}}",
         "missing": "{{nope}}", "nested": ["{{id}}"]},
        context,
    )
    assert rendered["id"] == "42"
    assert rendered["q"] == "cats"
    assert rendered["name"] == "Ada"
    assert rendered["missing"] == "{{nope}}"  # unknown placeholders stay visible
    assert rendered["nested"] == ["42"]


def test_render_body_non_string_passthrough():
    assert render_body(42, {}) == 42
    assert render_body(None, {}) is None


def test_build_context_precedence():
    # path params beat query params on name collision
    context = build_context({"id": "path"}, {"id": "query"}, None)
    assert context["id"] == "path"
    assert context["now"].endswith("Z")
    assert len(context["uuid"]) == 36


# -- live server ---------------------------------------------------------

@pytest.fixture()
def live(tmp_path):
    routes = load_routes(
        write_routes(
            tmp_path,
            {
                "routes": [
                    {"method": "GET", "path": "/health", "body": {"status": "ok"}},
                    {"method": "GET", "path": "/users/{id}",
                     "body": {"id": "{{id}}", "at": "{{now}}"}},
                    {"method": "GET", "path": "/search",
                     "body": {"q": "{{q}}"}},
                    {"method": "POST", "path": "/echo", "status": 201,
                     "body": {"got": "{{body.name}}", "id": "{{uuid}}"}},
                    {"method": "DELETE", "path": "/users/{id}", "status": 204},
                    {"method": "GET", "path": "/slow", "delay_ms": 150,
                     "body": {"slow": True}},
                    {"method": "GET", "path": "/text", "body": "hello there"},
                ]
            },
        )
    )
    server, _thread = serve_in_thread(routes, port=0, quiet=True)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()
    server.server_close()


def _request(base, path, method="GET", data=None, headers=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=data,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


def test_live_health(live):
    status, ctype, body = _request(live, "/health")
    assert status == 200
    assert "application/json" in ctype
    assert json.loads(body) == {"status": "ok"}


def test_live_path_param_and_now(live):
    status, _, body = _request(live, "/users/99")
    assert status == 200
    data = json.loads(body)
    assert data["id"] == "99"
    assert data["at"].endswith("Z")


def test_live_query_param(live):
    status, _, body = _request(live, "/search?q=cats")
    assert json.loads(body) == {"q": "cats"}


def test_live_post_echo_and_uuid(live):
    status, _, body = _request(
        live, "/echo", method="POST",
        data=json.dumps({"name": "Ada"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 201
    data = json.loads(body)
    assert data["got"] == "Ada"
    assert len(data["id"]) == 36


def test_live_404_and_405(live):
    status, _, body = _request(live, "/nope")
    assert status == 404
    assert json.loads(body)["error"] == "route not found"
    status, _, body = _request(live, "/users/7", method="PUT")
    assert status == 405
    assert json.loads(body)["error"] == "method not allowed"


def test_live_204_no_body(live):
    status, _, body = _request(live, "/users/7", method="DELETE")
    assert status == 204
    assert body == b""


def test_live_delay(live):
    started = time.monotonic()
    status, _, _ = _request(live, "/slow")
    assert status == 200
    assert time.monotonic() - started >= 0.15


def test_live_plain_text_body(live):
    status, ctype, body = _request(live, "/text")
    assert status == 200
    assert "text/plain" in ctype
    assert body == b"hello there"


# -- CLI -----------------------------------------------------------------

def test_cli_init_writes_example(tmp_path, monkeypatch):
    from mockapi.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["--init"]) == 0
    data = json.loads((tmp_path / "routes.json").read_text())
    assert len(data["routes"]) == 6
    assert main(["--init"]) == 2  # refuses to overwrite


def test_cli_print_routes(tmp_path, capsys):
    from mockapi.cli import main

    path = write_routes(tmp_path, {"routes": [{"method": "GET", "path": "/ping"}]})
    assert main([str(path), "--print-routes"]) == 0
    out = capsys.readouterr().out
    assert "1 route(s)" in out
    assert "GET" in out and "/ping" in out


def test_cli_missing_file_returns_2(tmp_path, capsys):
    from mockapi.cli import main

    assert main([str(tmp_path / "nope.json")]) == 2
    assert "not found" in capsys.readouterr().err
