"""Unit test for the container health endpoint.

Uses http.client directly — no FastAPI / TestClient needed.
"""
from __future__ import annotations

import threading
import time
from http.client import HTTPConnection
from http.server import HTTPServer

from streaming.container_entrypoint import _HealthHandler, _start_health_server


def _start() -> HTTPServer:
    """Start the health server on an ephemeral port for the test."""
    import os
    os.environ["PORT"] = "0"
    # Override bind address to localhost via a custom factory.
    return _start_health_server()


def test_health_endpoint_returns_ok_json():
    # Start on a fixed port for the test so we can hit it.
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert '"status":"ok"' in body
        assert '"service":"finnhub-publisher"' in body
    finally:
        server.shutdown()


def test_health_endpoint_returns_404_for_unknown_route():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/this-does-not-exist")
        resp = conn.getresponse()
        assert resp.status == 404
    finally:
        server.shutdown()
