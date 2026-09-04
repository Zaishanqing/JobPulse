"""Small monitoring HTTP server for the independent Worker process."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Protocol

from app.infrastructure.metrics import MetricsRegistry


class WorkerState(Protocol):
    @property
    def is_stopping(self) -> bool: ...

    @property
    def readiness_status(self) -> str: ...


class WorkerMonitoringServer:
    def __init__(
        self,
        metrics: MetricsRegistry,
        worker: WorkerState,
        *,
        host: str = "0.0.0.0",
        port: int = 9091,
    ) -> None:
        self._server = ThreadingHTTPServer((host, port), self._handler(metrics, worker))
        self._thread = Thread(
            target=self._server.serve_forever,
            name="matching-worker-monitoring",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @staticmethod
    def _handler(metrics: MetricsRegistry, worker: WorkerState):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                if self.path == "/metrics":
                    self._send(HTTPStatus.OK, metrics.render(), "text/plain; version=0.0.4")
                elif self.path == "/health/live":
                    self._send(HTTPStatus.OK, '{"status":"alive"}', "application/json")
                elif self.path == "/health/ready":
                    value = worker.readiness_status
                    status = (
                        HTTPStatus.OK
                        if value == "ready"
                        else HTTPStatus.SERVICE_UNAVAILABLE
                    )
                    self._send(status, f'{{"status":"{value}"}}', "application/json")
                else:
                    self._send(HTTPStatus.NOT_FOUND, "not found", "text/plain")

            def _send(self, status: HTTPStatus, body: str, content_type: str) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler
