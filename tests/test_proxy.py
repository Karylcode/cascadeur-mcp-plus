"""Tests for proxy.py with fake local servers (no Cascadeur needed).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    """A tiny HTTP server whose behaviour is defined per role."""

    def __init__(self, role: str, port: int | None = None, on_run=None):
        self.role = role
        self.on_run = on_run
        self.posts: list[tuple[str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if outer.role == "plus":
                    self._send(200, {"ok": True, "server": "cascadeur-mcp-plus", "version": "test"})
                elif outer.role == "official":
                    self._send(
                        200,
                        {
                            "ok": True,
                            "version": "0.3.1",
                            "idle_subscription": 0,
                            "message_subscription": 0,
                            "queued": 0,
                        },
                    )
                else:  # some unrelated local service
                    self._send(401, {"error": "unauthorized"})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                outer.posts.append((self.path, payload))
                if outer.role == "plus" and self.path == "/mcp":
                    if payload.get("method") == "tools/list":
                        self._send(
                            200,
                            {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": [{"name": "live_tool"}]}},
                        )
                    else:
                        self._send(
                            200,
                            {
                                "jsonrpc": "2.0",
                                "id": payload.get("id"),
                                "result": {"content": [{"type": "text", "text": "forwarded"}]},
                            },
                        )
                elif outer.role == "official" and self.path == "/run":
                    if outer.on_run:
                        outer.on_run(payload.get("code", ""))
                    self._send(200, {"ok": True, "value": ""})
                else:
                    self._send(404, {"error": "no route"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port or 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def load_proxy(plus_url: str, official_url: str, manifest: str | None = None):
    os.environ["CASCADEUR_PLUS_URL"] = plus_url
    os.environ["CASCADEUR_OFFICIAL_URL"] = official_url
    spec = importlib.util.spec_from_file_location(f"proxy_under_test_{free_port()}", os.path.join(ROOT, "proxy.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if manifest:
        module.MANIFEST = manifest
    module.log = lambda message: None  # keep test output clean
    return module


def call(proxy, name="get_scene_info", arguments=None):
    return proxy.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    )


def text_of(response) -> str:
    return " ".join(c.get("text", "") for c in response["result"]["content"])


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.servers: list[FakeServer] = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)

    def serve(self, role, port=None, on_run=None) -> FakeServer:
        server = FakeServer(role, port, on_run)
        self.servers.append(server)
        return server

    def test_identify_distinguishes_servers(self):
        plus, official, other = self.serve("plus"), self.serve("official"), self.serve("other")
        proxy = load_proxy(plus.url, official.url)
        self.assertEqual(proxy.identify(plus.url), "plus")
        self.assertEqual(proxy.identify(official.url), "official")
        self.assertEqual(proxy.identify(other.url), "other")
        self.assertIsNone(proxy.identify(f"http://127.0.0.1:{free_port()}"))

    def test_initialize_echoes_supported_protocol(self):
        proxy = load_proxy("http://127.0.0.1:1", "http://127.0.0.1:1")
        response = proxy.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(response["result"]["serverInfo"]["name"], "cascadeur-mcp-plus-proxy")

    def test_forwards_to_verified_server(self):
        plus, official = self.serve("plus"), self.serve("official")
        proxy = load_proxy(plus.url, official.url)
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual([p for p, _ in plus.posts], ["/mcp"])
        self.assertEqual(official.posts, [])

    def test_starts_server_through_official_when_down(self):
        plus_port = free_port()
        codes = []

        def start_plus(code):
            codes.append(code)
            self.serve("plus", plus_port)

        official = self.serve("official", on_run=start_plus)
        proxy = load_proxy(f"http://127.0.0.1:{plus_port}", official.url)
        response = call(proxy)
        self.assertIn("forwarded", text_of(response))
        self.assertIn("started MCP Plus automatically", text_of(response))
        self.assertEqual(len(codes), 1)
        self.assertIn("start_mcp_plus.py", codes[0])
        self.assertIn(f'"MCP_PLUS_PORT": {plus_port}', codes[0])

    def test_never_sends_tool_calls_to_another_program(self):
        other, official = self.serve("other"), self.serve("official")
        proxy = load_proxy(other.url, official.url)
        response = call(proxy, "run_python", {"code": "print('secret')"})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("used by another program", text_of(response))
        self.assertEqual(other.posts, [], "nothing may be POSTed to an unrelated program")
        self.assertEqual(official.posts, [])

    def test_refuses_to_bootstrap_through_unverified_official_port(self):
        other = self.serve("other")
        proxy = load_proxy(f"http://127.0.0.1:{free_port()}", other.url)
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Cascadeur's script server", text_of(response))
        self.assertEqual(other.posts, [])

    def test_reports_when_cascadeur_is_not_running(self):
        proxy = load_proxy(f"http://127.0.0.1:{free_port()}", f"http://127.0.0.1:{free_port()}")
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Start script server", text_of(response))

    def test_tools_list_uses_cache_when_down_and_refreshes_when_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "tools_manifest.json")
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump([{"name": "cached_tool"}], handle)
            down = load_proxy(f"http://127.0.0.1:{free_port()}", f"http://127.0.0.1:{free_port()}", manifest)
            listed = down.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual([t["name"] for t in listed["result"]["tools"]], ["cached_tool"])

            plus = self.serve("plus")
            up = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}", manifest)
            listed = up.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
            self.assertEqual([t["name"] for t in listed["result"]["tools"]], ["live_tool"])
            with open(manifest, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), [{"name": "live_tool"}])


if __name__ == "__main__":
    sys.exit(unittest.main())
