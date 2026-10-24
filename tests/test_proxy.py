"""Tests for proxy.py with fake local servers (no Cascadeur needed).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_port_range(count: int) -> list[int]:
    """Return count consecutive free localhost ports (the proxy scans a fixed port range)."""
    for _ in range(200):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        holders = []
        try:
            for offset in range(count):
                holder = socket.socket()
                holders.append(holder)
                holder.bind(("127.0.0.1", base + offset))
            return [base + offset for offset in range(count)]
        except OSError:
            continue
        finally:
            for holder in holders:
                holder.close()
    raise RuntimeError(f"could not find {count} free consecutive ports")


PROBE_MARKER = "__CASCADEUR_MCP_PLUS__"


def probe_reply(port=None, pid=4242) -> dict:
    """The official server's /run reply to the proxy's "where is MCP Plus" probe."""
    return {"ok": True, "messages": [{"level": "Info", "text": PROBE_MARKER + json.dumps({"pid": pid, "port": port})}]}


def is_start_code(code: str) -> bool:
    return "start_mcp_plus.py" in code


class FakeServer:
    """A tiny HTTP server whose behaviour is defined per role. health is merged into /health."""

    def __init__(self, role: str, port: int | None = None, on_run=None, delay: float = 0.0, health: dict | None = None):
        self.role = role
        self.on_run = on_run
        self.delay = delay
        self.health = health or {}
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
                    self._send(200, {"ok": True, "server": "cascadeur-mcp-plus", "version": "test", **outer.health})
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
                    response = outer.on_run(payload.get("code", "")) if outer.on_run else None
                    self._send(200, response if isinstance(response, dict) else {"ok": True, "value": ""})
                else:
                    self._send(404, {"error": "no route"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port or 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

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
    spec = importlib.util.spec_from_file_location(
        f"proxy_under_test_{free_port()}", os.path.join(ROOT, "cascadeur_mcp_plus_cli", "proxy.py")
    )
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

    def serve(self, role, port=None, on_run=None, delay=0.0, health=None) -> FakeServer:
        server = FakeServer(role, port, on_run, delay, health=health)
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

        def on_run(code):
            if not is_start_code(code):
                return probe_reply(port=None)
            codes.append(code)
            self.serve("plus", plus_port)

        official = self.serve("official", on_run=on_run)
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
            self.assertEqual(
                [t["name"] for t in listed["result"]["tools"]],
                ["cached_tool", "list_instances", "set_active_instance"],
            )

            plus = self.serve("plus")
            up = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}", manifest)
            listed = up.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
            self.assertEqual(
                [t["name"] for t in listed["result"]["tools"]],
                ["live_tool", "list_instances", "set_active_instance"],
            )
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

        def recording(message, timeout, base=None):
            seen.append(timeout)
            return original(message, timeout, base)

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

        def on_run(code):
            if not is_start_code(code):
                return probe_reply(port=None)
            codes.append(code)
            self.serve("plus", plus_port)

        official = self.serve("official", on_run=on_run)
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

        def recording(message, timeout, base=None):
            seen.append(timeout)
            return original(message, timeout, base)

        proxy.forward = recording
        read_resource(proxy)
        self.assertEqual(seen, [40.0])

    def test_refuses_to_restart_a_window_that_runs_mcp_plus_on_another_port(self):
        target = free_port()
        other_port = free_port()
        codes = []

        def on_run(code):
            if is_start_code(code):
                codes.append(code)
                return None
            return probe_reply(port=other_port)

        official = self.serve("official", on_run=on_run)
        proxy = load_proxy(f"http://127.0.0.1:{target}", official.url)
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("already runs MCP Plus on port", text_of(response))
        self.assertIn(str(other_port), text_of(response))
        self.assertIn("render/mocap", text_of(response))
        self.assertEqual(codes, [], "the start script must not run in that window")
        self.assertEqual([path for path, _ in official.posts], ["/run"])

    def test_probe_timeout_starts_nothing(self):
        target = free_port()
        official = self.serve(
            "official", on_run=lambda code: {"ok": False, "error": "Timed out waiting for Cascadeur idle event"}
        )
        proxy = load_proxy(f"http://127.0.0.1:{target}", official.url)
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("did not answer the MCP Plus status probe", text_of(response))
        self.assertIn("Nothing was started", text_of(response))
        self.assertEqual([path for path, _ in official.posts], ["/run"])

    def test_an_official_server_without_the_probe_signal_still_starts(self):
        target = free_port()
        codes = []

        def on_run(code):
            if is_start_code(code):
                codes.append(code)
                self.serve("plus", target)
            return {"ok": True, "value": ""}  # no printed lines: an older official server

        official = self.serve("official", on_run=on_run)
        proxy = load_proxy(f"http://127.0.0.1:{target}", official.url)
        response = call(proxy)
        self.assertIn("forwarded", text_of(response))
        self.assertEqual(len(codes), 1)
        self.assertIn(f'"MCP_PLUS_PORT": {target}', codes[0])


class DiscoveryTests(unittest.TestCase):
    """Port probing must be parallel, short and briefly cached (T53 review)."""

    def setUp(self):
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)

    def proxy_with_ports(self, count: int = 4):
        base = free_port_range(count)[0]
        proxy = load_proxy(f"http://127.0.0.1:{base}", f"http://127.0.0.1:{free_port()}")
        proxy.DEFAULT_PORT = base
        proxy.INSTANCE_PORT_COUNT = count
        return proxy, [base + offset for offset in range(count)]

    def test_probes_all_ports_at_the_same_time(self):
        proxy, ports = self.proxy_with_ports(4)
        barrier = threading.Barrier(len(ports), timeout=5)

        def fake_health(base, timeout=1.5):
            barrier.wait()  # only completes when every probe runs concurrently
            return None

        proxy.plus_health = fake_health
        self.assertEqual(proxy.discover_instances(force=True), [])
        self.assertEqual(len(proxy.candidate_ports()), len(ports))

    def test_probes_use_the_short_timeout(self):
        proxy, ports = self.proxy_with_ports(2)
        seen = []

        def fake_health(base, timeout=1.5):
            seen.append((base, timeout))
            return None

        proxy.plus_health = fake_health
        proxy.discover_instances(force=True)
        self.assertEqual({timeout for _, timeout in seen}, {proxy.PROBE_TIMEOUT})
        self.assertLessEqual(proxy.PROBE_TIMEOUT, 1.0)
        self.assertEqual(sorted(base for base, _ in seen), [f"http://127.0.0.1:{port}" for port in ports])

    def test_discovery_is_cached_for_a_few_seconds(self):
        proxy, ports = self.proxy_with_ports(2)
        clock = [1000.0]
        proxy._monotonic = lambda: clock[0]
        probes = []

        def fake_health(base, timeout=1.5):
            probes.append(base)
            return None

        proxy.plus_health = fake_health
        proxy.discover_instances()
        self.assertEqual(len(probes), len(ports))
        clock[0] += 1.0
        proxy.discover_instances()
        self.assertEqual(len(probes), len(ports), "the scan inside DISCOVERY_TTL must be reused")
        clock[0] += proxy.DISCOVERY_TTL + 0.5
        proxy.discover_instances()
        self.assertEqual(len(probes), 2 * len(ports))
        self.assertGreaterEqual(proxy.DISCOVERY_TTL, 5.0)
        self.assertLessEqual(proxy.DISCOVERY_TTL, 10.0)

    def test_invalidate_forces_a_new_scan(self):
        proxy, ports = self.proxy_with_ports(2)
        clock = [1000.0]
        proxy._monotonic = lambda: clock[0]
        probes = []

        def fake_health(base, timeout=1.5):
            probes.append(base)
            return None

        proxy.plus_health = fake_health
        proxy.discover_instances()
        proxy.invalidate_instances()
        proxy.discover_instances()
        self.assertEqual(len(probes), 2 * len(ports))


class MultiInstanceTests(unittest.TestCase):
    """Two fake MCP Plus servers stand in for two Cascadeur windows (T53)."""

    def setUp(self):
        self.servers: list[FakeServer] = []
        self.tmp = tempfile.mkdtemp(prefix="t53-proxy-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for server in self.servers:
            server.close()
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)

    def serve(self, role, port=None, on_run=None, health=None) -> FakeServer:
        server = FakeServer(role, port, on_run, health=health)
        self.servers.append(server)
        return server

    def two_instances(self):
        ports = free_port_range(2)
        first = self.serve(
            "plus",
            ports[0],
            health={
                "pid": 8765432,
                "scene": {"name": "first.casc", "path": r"C:\work\first.casc"},
                "cascadeur_version": "2026.2.3",
            },
        )
        second = self.serve(
            "plus",
            ports[1],
            health={
                "pid": 7654321,
                "scene": {"name": "second.casc", "path": r"C:\work\second.casc"},
                "cascadeur_version": "2026.2.3",
            },
        )
        official = self.serve("official")
        proxy = load_proxy(
            first.url,
            official.url,
            manifest=os.path.join(self.tmp, "tools_manifest.json"),
            resource_manifest=os.path.join(self.tmp, "resources_manifest.json"),
        )
        proxy.DEFAULT_PORT = ports[0]
        proxy.INSTANCE_PORT_COUNT = 2
        return proxy, first, second, official

    def local(self, proxy, name, arguments=None) -> dict:
        response = proxy.handle(
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
        )
        return json.loads(text_of(response))

    def test_list_instances_finds_both_servers(self):
        proxy, first, second, official = self.two_instances()
        result = self.local(proxy, "list_instances")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual([entry["port"] for entry in result["instances"]], [first.port, second.port])
        self.assertEqual([entry["pid"] for entry in result["instances"]], [8765432, 7654321])
        self.assertEqual(result["instances"][1]["scene"]["name"], "second.casc")
        self.assertEqual(result["instances"][1]["cascadeur_version"], "2026.2.3")
        self.assertTrue(result["instances"][0]["default"])
        self.assertEqual(result["active"]["port"], first.port)
        self.assertEqual(official.posts, [])

    def test_set_active_instance_routes_later_calls(self):
        proxy, first, second, _ = self.two_instances()
        result = self.local(proxy, "set_active_instance", {"instance": second.port})
        self.assertTrue(result["ok"])
        self.assertEqual(result["active"]["port"], second.port)
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual([path for path, _ in second.posts], ["/mcp"])
        self.assertEqual(first.posts, [])
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual(len(second.posts), 2)
        self.assertEqual(first.posts, [])

    def test_instance_argument_routes_one_call_only(self):
        proxy, first, second, _ = self.two_instances()
        self.assertEqual(text_of(call(proxy, arguments={"instance": second.port})), "forwarded")
        self.assertEqual(len(second.posts), 1)
        self.assertNotIn("instance", second.posts[0][1]["params"]["arguments"])
        self.assertEqual(first.posts, [])
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual(len(first.posts), 1)
        self.assertEqual(len(second.posts), 1)

    def test_explicit_default_instance_still_starts_via_the_official_server(self):
        port = free_port()
        codes = []

        def on_run(code):
            if not is_start_code(code):
                return probe_reply(port=None)
            codes.append(code)
            self.serve("plus", port)

        official = self.serve("official", on_run=on_run)
        proxy = load_proxy(
            f"http://127.0.0.1:{port}",
            official.url,
            manifest=os.path.join(self.tmp, "tools_manifest.json"),
        )
        proxy.DEFAULT_PORT = port
        proxy.INSTANCE_PORT_COUNT = 1
        response = call(proxy, arguments={"instance": port})
        self.assertIn("forwarded", text_of(response))
        self.assertEqual(len(codes), 1)
        self.assertIn(f'"MCP_PLUS_PORT": {port}', codes[0])

    def test_instance_can_be_a_pid_or_a_scene_name(self):
        proxy, first, second, _ = self.two_instances()
        chosen = self.local(proxy, "set_active_instance", {"instance": 7654321})
        self.assertEqual(chosen["active"]["port"], second.port)
        chosen = self.local(proxy, "set_active_instance", {"instance": "pid:8765432"})
        self.assertEqual(chosen["active"]["port"], first.port)
        chosen = self.local(proxy, "set_active_instance", {"instance": "second"})
        self.assertEqual(chosen["active"]["port"], second.port)

    def test_ambiguous_scene_name_is_an_error(self):
        proxy, first, second, _ = self.two_instances()
        result = self.local(proxy, "set_active_instance", {"instance": ".casc"})
        self.assertFalse(result["ok"])
        self.assertIn("matches 2 instances", result["error"])
        self.assertEqual(self.local(proxy, "list_instances")["active"]["port"], first.port)

    def test_instance_that_is_gone_is_not_silently_retargeted(self):
        proxy, first, second, official = self.two_instances()
        gone = second.port
        second.close()
        self.servers.remove(second)
        response = call(proxy, arguments={"instance": gone})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("No MCP Plus instance matches", text_of(response))
        self.assertIn("Python Console", text_of(response))
        self.assertEqual(first.posts, [])
        self.assertEqual(official.posts, [])

    def test_closed_active_instance_reports_instead_of_using_the_other(self):
        proxy, first, second, official = self.two_instances()
        self.local(proxy, "set_active_instance", {"instance": second.port})
        second.close()
        self.servers.remove(second)
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("is not running at", text_of(response))
        self.assertIn("8765", text_of(response))
        self.assertEqual(first.posts, [])
        self.assertEqual(official.posts, [])

    def test_set_active_instance_default_goes_back_to_the_first_instance(self):
        proxy, first, second, _ = self.two_instances()
        self.local(proxy, "set_active_instance", {"instance": second.port})
        result = self.local(proxy, "set_active_instance", {"instance": "default"})
        self.assertEqual(result["active"]["port"], first.port)
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual(len(first.posts), 1)
        self.assertEqual(second.posts, [])

    def test_list_instances_ignores_other_programs_and_the_official_server(self):
        ports = free_port_range(3)
        plus = self.serve("plus", ports[0], health={"pid": 3333})
        official = self.serve("official", ports[1])
        other = self.serve("other", ports[2])
        proxy = load_proxy(plus.url, official.url)
        proxy.DEFAULT_PORT = ports[0]
        proxy.INSTANCE_PORT_COUNT = 3
        result = self.local(proxy, "list_instances")
        self.assertEqual([entry["port"] for entry in result["instances"]], [ports[0]])
        self.assertEqual(official.posts, [])
        self.assertEqual(other.posts, [])

    def test_tools_list_advertises_the_instance_argument_and_tools(self):
        proxy, first, second, _ = self.two_instances()
        listed = proxy.handle({"jsonrpc": "2.0", "id": 22, "method": "tools/list"})
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertIn("list_instances", tools)
        self.assertIn("set_active_instance", tools)
        self.assertIn("instance", tools["live_tool"]["inputSchema"]["properties"])
        self.assertIn("instance", tools["set_active_instance"]["inputSchema"]["properties"])

    def test_manifest_keeps_the_server_tool_list_unchanged(self):
        manifest = os.path.join(self.tmp, "tools_manifest.json")
        ports = free_port_range(1)
        plus = self.serve("plus", ports[0])
        proxy = load_proxy(plus.url, f"http://127.0.0.1:{free_port()}", manifest)
        proxy.DEFAULT_PORT = ports[0]
        proxy.INSTANCE_PORT_COUNT = 1
        proxy.handle({"jsonrpc": "2.0", "id": 23, "method": "tools/list"})
        with open(manifest, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), [{"name": "live_tool"}])

    def test_one_instance_keeps_the_old_single_call_behavior(self):
        proxy, first, second, official = self.two_instances()
        second.close()
        self.servers.remove(second)
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual(len(first.posts), 1)
        self.assertEqual(official.posts, [])

    def test_first_call_adopts_the_default_pid(self):
        proxy, first, second, _ = self.two_instances()
        self.assertIsNone(proxy.active_instance()["pid"])
        self.assertEqual(text_of(call(proxy)), "forwarded")
        self.assertEqual(proxy.active_instance()["pid"], 8765432)

    def test_active_instance_taken_over_by_a_new_cascadeur_is_refused(self):
        proxy, first, second, _ = self.two_instances()
        self.local(proxy, "set_active_instance", {"instance": second.port})
        old_port = second.port
        second.close()
        self.servers.remove(second)
        replacement = self.serve("plus", old_port, health={"pid": 9999999, "scene": {"name": "other.casc", "path": ""}})
        response = call(proxy)
        self.assertTrue(response["result"]["isError"])
        text = text_of(response)
        self.assertIn("different Cascadeur", text)
        self.assertIn("9999999", text)
        self.assertIn("list_instances", text)
        self.assertEqual(replacement.posts, [], "the new Cascadeur must not receive the call")
        self.assertEqual(first.posts, [])

    def test_per_call_instance_is_checked_for_a_takeover_too(self):
        proxy, first, second, _ = self.two_instances()
        self.assertEqual(self.local(proxy, "list_instances")["count"], 2)  # cache both pids
        old_port = second.port
        second.close()
        self.servers.remove(second)
        replacement = self.serve("plus", old_port, health={"pid": 9999999, "scene": {"name": "other.casc", "path": ""}})
        response = call(proxy, arguments={"instance": old_port})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("different Cascadeur", text_of(response))
        self.assertEqual(replacement.posts, [])

    def test_health_without_a_pid_still_forwards(self):
        ports = free_port_range(1)
        plus = self.serve("plus", ports[0], health={"scene": {"name": "old.casc", "path": ""}})
        official = self.serve("official")
        proxy = load_proxy(plus.url, official.url, manifest=os.path.join(self.tmp, "tools_manifest.json"))
        proxy.DEFAULT_PORT = ports[0]
        proxy.INSTANCE_PORT_COUNT = 1
        chosen = self.local(proxy, "set_active_instance", {"instance": ports[0]})
        self.assertIsNone(chosen["active"]["pid"])
        self.assertEqual(text_of(call(proxy)), "forwarded")

    def test_a_dead_instance_invalidates_the_discovery_cache(self):
        proxy, first, second, _ = self.two_instances()
        self.assertEqual(self.local(proxy, "list_instances")["count"], 2)
        self.assertIsNotNone(proxy._discovery_cache["instances"])
        second.close()
        self.servers.remove(second)
        response = call(proxy, arguments={"instance": second.port})
        self.assertTrue(response["result"]["isError"])
        self.assertIsNone(proxy._discovery_cache["instances"])
        self.assertEqual(self.local(proxy, "list_instances")["count"], 1)

    def test_list_instances_reuses_one_scan(self):
        proxy, first, second, _ = self.two_instances()
        probes = []
        original = proxy.plus_health

        def counting(base, timeout=1.5):
            probes.append(base)
            return original(base, timeout)

        proxy.plus_health = counting
        self.assertEqual(self.local(proxy, "list_instances")["count"], 2)
        self.assertEqual(self.local(proxy, "list_instances")["count"], 2)
        self.assertEqual(len(probes), 2)


class ConfiguredHostTests(unittest.TestCase):
    """CASCADEUR_PLUS_URL keeps its host (WSL, a LAN address); never hardcode 127.0.0.1 (T53 review)."""

    def setUp(self):
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        os.environ.pop("CASCADEUR_PLUS_URL", None)
        os.environ.pop("CASCADEUR_OFFICIAL_URL", None)

    def test_discovery_probes_the_configured_host(self):
        proxy = load_proxy("http://cascadeur.example:48766", "http://cascadeur.example:8765")
        proxy.DEFAULT_PORT = 48766
        proxy.INSTANCE_PORT_COUNT = 2
        seen = []

        def fake_http(url, payload=None, timeout=5.0):
            seen.append(url)
            if url == "http://cascadeur.example:48766/health":
                return {"ok": True, "server": "cascadeur-mcp-plus", "version": "0.4.0", "pid": 7}
            raise urllib.error.URLError("connection refused")

        proxy.http_json = fake_http
        instances = proxy.discover_instances(force=True)
        self.assertEqual(
            [(entry["port"], entry["url"]) for entry in instances], [(48766, "http://cascadeur.example:48766")]
        )
        self.assertTrue(all(url.startswith("http://cascadeur.example:") for url in seen), seen)
        self.assertIn("http://cascadeur.example:48767/health", seen)

    def test_default_selection_keeps_the_configured_host(self):
        proxy = load_proxy("http://cascadeur.example:48766", "http://cascadeur.example:8765")

        def fake_http(url, payload=None, timeout=5.0):
            return {"ok": True, "server": "cascadeur-mcp-plus", "version": "0.4.0", "pid": 7}

        proxy.http_json = fake_http
        response = proxy.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "set_active_instance", "arguments": {"instance": "default"}},
            }
        )
        result = json.loads(text_of(response))
        self.assertTrue(result["ok"])
        self.assertEqual(result["active"]["url"], "http://cascadeur.example:48766")
        self.assertEqual(proxy.active_url(), "http://cascadeur.example:48766")

    def test_a_same_port_on_another_host_is_not_the_default(self):
        proxy = load_proxy("http://cascadeur.example:48766", "http://cascadeur.example:8765")
        self.assertTrue(proxy._is_default("http://cascadeur.example:48766"))
        self.assertFalse(proxy._is_default("http://127.0.0.1:48766"))


if __name__ == "__main__":
    sys.exit(unittest.main())
