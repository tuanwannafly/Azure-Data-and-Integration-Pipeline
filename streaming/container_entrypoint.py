"""Container entrypoint: run the long-lived publisher alongside a tiny HTTP
health server.

Azure Container Apps' liveness probe hits GET /healthz on $PORT (default 8080).
We start a `http.server` thread before launching the publisher so the probe
always sees a 200 OK as long as the container is alive — it does NOT verify
that the Finnhub WebSocket is currently up (the publisher itself reconnects
with exponential backoff and we don't want temporary Finnhub outages to
restart the pod).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# When the entrypoint is loaded by Container Apps we are at the project root
# so the streaming package is importable as `streaming`.
sys.path.insert(0, os.getcwd())

logger = logging.getLogger("entrypoint")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — stdlib API name
        if self.path not in ("/healthz", "/"):
            self.send_error(404)
            return
        body = b'{"status":"ok","service":"finnhub-publisher"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silence the default access log; we use the publisher's structured logger.
    def log_message(self, format, *args):  # noqa: A002 — stdlib API name
        return


def _start_health_server() -> HTTPServer:
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    logger.info("health endpoint listening on :%s", port)
    return server


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _start_health_server()
    # Import here so health server starts even if the publisher fails to import.
    from streaming.finnhub_ws_publisher import FinnhubPublisher
    FinnhubPublisher().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
