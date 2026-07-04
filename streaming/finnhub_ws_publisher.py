"""Finnhub real-time WebSocket publisher (US-07).

Connects to the Finnhub WebSocket endpoint, subscribes to a configured list of
symbols (US stocks + crypto), and publishes every normalized trade tick to the
Service Bus queue `market-ticks` for the Azure Function subscriber to upsert.

Architecture note (see GitPlan): the WebSocket connection is long-lived, so the
publisher must run on an always-on host (Azure Container App / App Service
WebJob), NOT an Azure Function Consumption plan (which has execution-time caps).
Only the consumer side (Service Bus trigger) uses a Function.

Resilience (BR-05): on disconnect the publisher reconnects with exponential
backoff and re-subscribes to the symbol list.

Env vars (BR-01):
  * FINNHUB_API_KEY          — token for wss://ws.finnhub.io?token=...
  * FINNHUB_SYMBOLS          — comma-separated symbols, e.g. "AAPL,BINANCE:BTCUSDT"
  * SERVICE_BUS_CONNECTION_STRING — Service Bus namespace connection string
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from websocket import create_connection

logger = logging.getLogger(__name__)

FINNHUB_WS_URL = "wss://ws.finnhub.io?token={token}"
DEFAULT_SYMBOL = "AAPL"
DEFAULT_QUEUE = "market-ticks"
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 60.0


def parse_symbols(raw: str | None) -> list[str]:
    """Parse the FINNHUB_SYMBOLS env var into a clean symbol list.

    Returns a sensible default (AAPL) when unset/empty so the publisher is
    runnable out of the box for a first smoke test.
    """
    if not raw:
        return [DEFAULT_SYMBOL]
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    return symbols or [DEFAULT_SYMBOL]


def build_ws_url(token: str) -> str:
    """Build the Finnhub WebSocket URL with the auth token."""
    return FINNHUB_WS_URL.format(token=token)


def _load_symbols() -> list[str]:
    return parse_symbols(os.getenv("FINNHUB_SYMBOLS"))


def normalize_trade_message(raw: str) -> list[dict[str, Any]]:
    """Normalize a raw Finnhub trade message into a list of tick dicts.

    Finnhub trade payload:
        {"type": "trade", "data": [{"s","p","v","t"}, ...]}

    Returns [] for non-trade frames, malformed JSON, or empty input.
    """
    if not raw:
        return []
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(msg, dict) or msg.get("type") != "trade":
        return []

    received_at = datetime.now(timezone.utc).isoformat()
    ticks: list[dict[str, Any]] = []
    for trade in msg.get("data", []):
        try:
            ticks.append(
                {
                    "symbol": trade["s"],
                    "price": float(trade["p"]),
                    "volume": float(trade["v"]),
                    "trade_timestamp": int(trade["t"]),
                    "received_at": received_at,
                }
            )
        except (KeyError, TypeError, ValueError):
            logger.debug("Skipping malformed trade entry: %r", trade)
    return ticks


def publish_tick(
    tick: dict[str, Any],
    queue: str = DEFAULT_QUEUE,
    *,
    sender: Any = None,
) -> None:
    """Publish one normalized tick to the Service Bus queue (BR-02 prep).

    If ``sender`` is provided (a Service Bus sender with ``send_messages``),
    use it directly — this avoids opening a new client per tick and makes the
    function trivially testable. Otherwise open a client from env.
    """
    body = ServiceBusMessage(json.dumps(tick))
    if sender is not None:
        sender.send_messages(body)
        return
    conn_str = os.getenv("SERVICE_BUS_CONNECTION_STRING")
    if not conn_str:
        raise ValueError("SERVICE_BUS_CONNECTION_STRING is not set (BR-01)")
    with ServiceBusClient.from_connection_string(conn_str) as client:
        with client.get_queue_sender(queue) as s:
            s.send_messages(body)


class FinnhubPublisher:
    """Long-lived publisher: connect → subscribe → publish → reconnect loop."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        queue: str = DEFAULT_QUEUE,
    ) -> None:
        self.symbols = symbols or _load_symbols()
        self.queue = queue
        api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key:
            raise ValueError("FINNHUB_API_KEY is not set (BR-01)")
        self.url = build_ws_url(api_key)

    def _subscribe(self, conn: Any) -> None:
        """Send a subscribe frame for each symbol."""
        for sym in self.symbols:
            frame = json.dumps({"type": "subscribe", "symbol": sym})
            conn.send(frame)
            logger.info("Subscribed to %s", sym)

    def run(
        self,
        *,
        max_messages: int | None = None,
        sender: Any | None = None,
    ) -> None:
        """Main loop: connect, subscribe, and forward ticks until stopped.

        Reconnects with exponential backoff on dropped connections (BR-05).
        If ``max_messages`` is set, the loop stops after publishing that many
        messages (used by tests / smoke runs).

        Args:
            max_messages: stop after publishing this many ticks (tests/smoke).
            sender: optional pre-built Service Bus sender for dependency
                injection in tests; if None a real sender is created lazily.
        """
        published = 0
        attempt = 0
        while True:
            try:
                conn = create_connection(self.url, timeout=15)
                self._subscribe(conn)
                attempt = 0  # reset backoff after a successful connect
                while True:
                    raw = conn.recv()
                    if raw in ("", None):
                        continue
                    for tick in normalize_trade_message(raw):
                        publish_tick(tick, queue=self.queue, sender=sender)
                        published += 1
                        if max_messages is not None and published >= max_messages:
                            conn.close()
                            return
            except KeyboardInterrupt:
                logger.info("Publisher stopped by user.")
                raise
            except Exception as exc:  # noqa: BLE001 — any disconnect → reconnect
                attempt += 1
                delay = min(RECONNECT_BASE_DELAY * (2 ** (attempt - 1)), RECONNECT_MAX_DELAY)
                logger.warning("Connection lost (%s); reconnecting in %.1fs", exc, delay)
                time.sleep(delay)

    def run_with_stream(self, stream: Iterable[str], sender: Any = None) -> int:
        """Process a pre-supplied message stream (testable, no socket needed).

        Returns the number of ticks published.
        """
        published = 0
        for raw in stream:
            for tick in normalize_trade_message(raw):
                publish_tick(tick, queue=self.queue, sender=sender)
                published += 1
        return published


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    FinnhubPublisher().run()
