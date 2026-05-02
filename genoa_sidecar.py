#!/usr/bin/env python3
"""Genoa sidecar API and dashboard for SPLAT."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
DASHBOARD = Path(__file__).with_name("dashboard.html")
ALLOWED_ORIGIN = os.getenv("GENOA_ALLOWED_ORIGIN", "*")


@dataclass
class RunStats:
    total_runs: int = 0
    success_runs: int = 0
    failed_runs: int = 0
    avg_runtime_seconds: float = 0.0
    last_run_at: str | None = None
    last_returncode: int | None = None

    @property
    def success_rate(self) -> float:
        return self.success_runs / self.total_runs if self.total_runs else 0.0


STATS = RunStats()
LOCK = Lock()


class GenoaHandler(BaseHTTPRequestHandler):
    server_version = "genoa-sidecar/1.2"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._add_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/dashboard"):
            self._serve_dashboard()
            return
        if self.path in ("/healthz", "/readyz"):
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/api/v1/stats":
            with LOCK:
                payload = asdict(STATS)
                payload["success_rate"] = STATS.success_rate
            self._send_json(HTTPStatus.OK, payload)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/v1/splat/run":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        payload = self._read_json_body()
        if isinstance(payload, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": payload})
            return

        command = build_splat_command(payload)
        if isinstance(command, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": command})
            return

        started = time.time()
        try:
            result = subprocess.run(
                command,
                cwd=WORKDIR,
                capture_output=True,
                text=True,
                timeout=int(payload.get("timeout_seconds", 120)),
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "splat run timed out"})
            return

        runtime_seconds = time.time() - started
        update_stats(result.returncode, runtime_seconds)

        self._send_json(
            HTTPStatus.OK,
            {
                "command": command,
                "command_string": shlex.join(command),
                "returncode": result.returncode,
                "runtime_seconds": runtime_seconds,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )

    def _read_json_body(self) -> dict | str:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return "request body is required"
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return f"invalid json: {exc}"

    def _serve_dashboard(self) -> None:
        if not DASHBOARD.exists():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "dashboard missing"})
            return
        data = DASHBOARD.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._add_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _add_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)

    def log_message(self, format: str, *args) -> None:
        return


def build_splat_command(payload: dict) -> list[str] | str:
    tx_qth = payload.get("tx_qth")
    if not tx_qth:
        return "tx_qth is required"

    command = [SPLAT_BIN, "-t", str(tx_qth)]
    if payload.get("rx_qth"):
        command.extend(["-r", str(payload["rx_qth"])])
    if payload.get("output_base"):
        command.extend(["-o", str(payload["output_base"])])
    for flag in payload.get("flags", []):
        command.append(str(flag))
    return command


def update_stats(returncode: int, runtime_seconds: float) -> None:
    with LOCK:
        previous_runtime_sum = STATS.avg_runtime_seconds * STATS.total_runs
        STATS.total_runs += 1
        if returncode == 0:
            STATS.success_runs += 1
        else:
            STATS.failed_runs += 1
        STATS.avg_runtime_seconds = (previous_runtime_sum + runtime_seconds) / STATS.total_runs
        STATS.last_run_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        STATS.last_returncode = returncode


if __name__ == "__main__":
    WORKDIR.mkdir(parents=True, exist_ok=True)
    print(f"Genoa sidecar listening on http://{HOST}:{PORT} (workdir={WORKDIR})")
    ThreadingHTTPServer((HOST, PORT), GenoaHandler).serve_forever()
