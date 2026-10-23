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
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    """A tiny HTTP server whose behaviour is defined per role."""

    def __init__(self, role: str, port: int | None = None, on_run=None, delay: float = 0.0):
        self.role = role
        self.on_run = on_run
        self.delay = delay
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
                    if outer.delay and payload.get("method") == "tools/call":
                        time.sleep(outer.delay)
                    if payload.get("method") == "tools/list":
                        self._send(
                            200,
                            {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": [{"name": "live_tool"}]}},
                        )
                    elif payload.get("method") == "resources/list":
                        self._send(
                            200,
                            {
                                "jsonrpc": "2.0",
                                "id": payload.get("id"),
                                "result": {
                                    "resources": [
                                        {
                                            "uri": "cascadeur://live",
                                            "name": "Live",
                                            "description": "live resource",
                                            "mimeType": "application/json",
                                        }
                                    ]
                                },
                            },
                        )
                    elif payload.get("method") == "resources/read":
                        uri = (payload.get("params") or {}).get("uri")
                        self._send(
                            200,
                            {
                                "jsonrpc": "2.0",
                                "id": payload.get("id"),
                                "result": {
                                    "contents": [{"uri": uri, "mimeType": "text/plain", "text": "forwarded resource"}]
                                },
                            },
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


def load_proxy(
    plus_url: str,
    official_url: str,
    manifest: str | None = None,
    resource_manifest: str | None = None,
):
    os.environ["CASCADEUR_PLUS_URL"] = plus_url
    os.environ["CASCADEUR_OFFICIAL_URL"] = official_url
    spec = importlib.util.spec_from_file_location(f"proxy_under_test_{free_port()}", os.path.join(ROOT, "proxy.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if manifest:
        module.MANIFEST = manifest
    if resource_manifest:
        module.RESOURCE_MANIFEST = resource_manifest
    module.log = lambda message: None  # keep test output clean
    return module


def call(proxy, name="get_scene_info", arguments=None):
    return proxy.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    )


def text_of(response) -> str:
    return " ".join(c.get("text", "") for c in response["result"]["content"])


def list_resources(proxy):
    return proxy.handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})


def read_resource(proxy, uri="cascadeur://scene"):
    return proxy.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/read", "params": {"uri": uri}})


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.servers: list[FakeServer] = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)

    def serve(self, role, port=None, on_run=None, delay=0.0) -> FakeServer:
        server = FakeServer(role, port, on_run, delay)
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

    def test_tool_timeout_uses_numeric_argument_with_grace(self):
        proxy = load_proxy("http://127.0.0.1:1", "http://127.0.0.1:1")
        self.assertEqual(proxy.tool_timeout({"timeout": 5}), 35.0)
        self.assertEqual(proxy.tool_timeout({"timeout": 1.5}), 31.5)
        self.assertEqual(proxy.tool_timeout({"timeout": 600}), 630.0)
        self.assertEqual(proxy.tool_timeout({"timeout": 900}), 630.0)
        self.assertEqual(proxy.tool_timeout({"timeout": 0}), 30.0)
        self.assertEqual(proxy.tool_timeout({"timeout": -5}), 30.0)

    def test_tool_timeout_defaults_for_unusable_values(self):
        proxy = load_proxy("http://127.0.0.1:1", "http://127.0.0.1:1")
        self.assertEqual(proxy.tool_timeout({}), 210.0)
        self.assertEqual(proxy.tool_timeout({"timeout": None}), 210.0)
        self.assertEqual(proxy.tool_timeout({"timeout": "60"}), 210.0)
        self.assertEqual(proxy.tool_timeout({"timeout": True}), 210.0)
        self.assertEqual(proxy.tool_timeout({"timeout": float("nan")}), 210.0)
        self.assertEqual(proxy.tool_timeout({"timeout": float("inf")}), 210.0)

    def test_tool_call_forwards_the_computed_timeout(self):
        plus = self.serve("plus")
        proxy = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}")
        proxy._verified_until = time.monotonic() + 60
        seen = []
        original = proxy.forward

        def recording(message, timeout):
            seen.append(timeout)
            return original(message, timeout)

        proxy.forward = recording
        self.assertEqual(text_of(call(proxy, "import_model", {"path": "x.fbx", "timeout": 5})), "forwarded")
        self.assertEqual(text_of(call(proxy, "render_image", {})), "forwarded")
        self.assertEqual(text_of(call(proxy, "batch", {"calls": [], "timeout": 900})), "forwarded")
        self.assertEqual(seen, [35.0, 210.0, 630.0])

    def test_tool_call_waits_past_a_short_tool_timeout(self):
        plus = self.serve("plus", delay=1.2)
        proxy = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}")
        proxy._verified_until = time.monotonic() + 60
        started = time.monotonic()
        response = call(proxy, "batch", {"timeout": 0.5})
        elapsed = time.monotonic() - started
        self.assertEqual(text_of(response), "forwarded")
        self.assertGreaterEqual(elapsed, 1.0)

    def test_initialize_advertises_resources(self):
        proxy = load_proxy("http://127.0.0.1:1", "http://127.0.0.1:1")
        response = proxy.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
        )
        capabilities = response["result"]["capabilities"]
        self.assertIn("tools", capabilities)
        self.assertIn("resources", capabilities)

    def test_resources_list_forwards_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "resources_manifest.json")
            plus = self.serve("plus")
            proxy = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}", resource_manifest=manifest)
            listed = list_resources(proxy)
            resources = listed["result"]["resources"]
            self.assertEqual([r["uri"] for r in resources], ["cascadeur://live"])
            with open(manifest, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), resources)

            # With Cascadeur closed the proxy answers from the cache it just wrote.
            down = load_proxy(
                f"http://127.0.0.1:{free_port()}",
                f"http://127.0.0.1:{free_port()}",
                resource_manifest=manifest,
            )
            cached = list_resources(down)
            self.assertEqual(cached["result"]["resources"], resources)

    def test_resources_list_uses_cache_without_starting_cascadeur(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = os.path.join(tmp, "resources_manifest.json")
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump([{"uri": "cascadeur://cached", "name": "Cached"}], handle)
            official = self.serve("official")
            proxy = load_proxy(f"http://127.0.0.1:{free_port()}", official.url, resource_manifest=manifest)
            listed = list_resources(proxy)
            self.assertEqual([r["uri"] for r in listed["result"]["resources"]], ["cascadeur://cached"])
            self.assertEqual(official.posts, [])

    def test_resources_list_never_talks_to_another_program(self):
        other = self.serve("other")
        proxy = load_proxy(other.url, f"http://127.0.0.1:{free_port()}")
        list_resources(proxy)
        self.assertEqual(other.posts, [], "nothing may be POSTed to an unrelated program")

    def test_resources_read_forwards_to_verified_server(self):
        plus, official = self.serve("plus"), self.serve("official")
        proxy = load_proxy(plus.url, official.url)
        response = read_resource(proxy, "cascadeur://scene")
        contents = response["result"]["contents"]
        self.assertEqual(contents[0]["uri"], "cascadeur://scene")
        self.assertEqual(contents[0]["text"], "forwarded resource")
        self.assertEqual([p for p, _ in plus.posts], ["/mcp"])
        self.assertEqual(official.posts, [])

    def test_resources_read_starts_server_through_official_when_down(self):
        plus_port = free_port()
        codes = []

        def start_plus(code):
            codes.append(code)
            self.serve("plus", plus_port)

        official = self.serve("official", on_run=start_plus)
        proxy = load_proxy(f"http://127.0.0.1:{plus_port}", official.url)
        response = read_resource(proxy)
        self.assertEqual(response["result"]["contents"][0]["text"], "forwarded resource")
        self.assertEqual(len(codes), 1)
        self.assertIn("start_mcp_plus.py", codes[0])
        self.assertIn(f'"MCP_PLUS_PORT": {plus_port}', codes[0])

    def test_resources_read_reports_when_cascadeur_is_not_running(self):
        proxy = load_proxy(f"http://127.0.0.1:{free_port()}", f"http://127.0.0.1:{free_port()}")
        response = read_resource(proxy)
        self.assertIn("error", response)
        self.assertIn("Start script server", response["error"]["message"])

    def test_resources_read_never_sends_to_another_program(self):
        other, official = self.serve("other"), self.serve("official")
        proxy = load_proxy(other.url, official.url)
        response = read_resource(proxy)
        self.assertIn("error", response)
        self.assertIn("used by another program", response["error"]["message"])
        self.assertEqual(other.posts, [], "nothing may be POSTed to an unrelated program")
        self.assertEqual(official.posts, [])

    def test_resources_templates_list_is_empty(self):
        proxy = load_proxy("http://127.0.0.1:1", "http://127.0.0.1:1")
        response = proxy.handle({"jsonrpc": "2.0", "id": 10, "method": "resources/templates/list"})
        self.assertEqual(response["result"], {"resourceTemplates": []})

    def test_resources_read_uses_a_40s_timeout(self):
        plus = self.serve("plus")
        proxy = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}")
        seen = []
        original = proxy.forward

        def recording(message, timeout):
            seen.append(timeout)
            return original(message, timeout)

        proxy.forward = recording
        read_resource(proxy)
        self.assertEqual(seen, [40.0])


if __name__ == "__main__":
    sys.exit(unittest.main())
