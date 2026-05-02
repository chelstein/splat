#!/usr/bin/env python3
"""Genoa sidecar HTTP service for running SPLAT! as an API endpoint."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()


class GenoaHandler(BaseHTTPRequestHandler):
    server_version = "genoa-sidecar/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/v1/splat/run":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {exc}"})
            return

        command = self._build_command(payload)
        if isinstance(command, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": command})
            return

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

        self._send_json(
            HTTPStatus.OK,
            {
                "command": command,
                "command_string": shlex.join(command),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )

    def _build_command(self, payload: dict) -> list[str] | str:
        tx_qth = payload.get("tx_qth")
        if not tx_qth:
            return "tx_qth is required"

        command = [SPLAT_BIN, "-t", str(tx_qth)]

        rx_qth = payload.get("rx_qth")
        if rx_qth:
            command.extend(["-r", str(rx_qth)])

        output = payload.get("output_base")
        if output:
            command.extend(["-o", str(output)])

        for flag in payload.get("flags", []):
            command.append(str(flag))

        return command

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    WORKDIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), GenoaHandler)
    print(f"Genoa sidecar listening on http://{HOST}:{PORT} (workdir={WORKDIR})")
    server.serve_forever()
