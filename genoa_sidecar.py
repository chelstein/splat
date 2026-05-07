#!/usr/bin/env python3
"""Genoa sidecar HTTP service for running SPLAT! as an API endpoint.

Flask + gunicorn version (so the DigitalOcean App Platform CMD
`gunicorn genoa_sidecar:app` can bind directly).  Routes and request /
response shape are unchanged from the original stdlib http.server
implementation:

  GET  /healthz              -> {"status": "ok"}
  GET  /version              -> sidecar metadata
  GET  /api/v1/stats         -> per-worker run counters (see RunStats)
  POST /api/v1/splat/run     -> runs ./splat with the given args

The SPLAT binary is invoked via subprocess against `WORKDIR`.  The
container is expected to have already built `./splat` (Dockerfile runs
`./configure && ./build && ./install`).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from flask import Flask, jsonify, request

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

app = Flask("genoa-sidecar")


class RunStats:
    """In-memory aggregator for /api/v1/splat/run invocations.

    Counts are PER-WORKER. Gunicorn pre-forks (default 2 workers in this
    deployment), so /api/v1/stats reflects whichever worker handled that
    GET, not a global view across the fleet. If a global view becomes
    important, swap this for Redis or Postgres-backed counters.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._success = 0
        self._failure = 0
        self._total_runtime = 0.0
        self._last_run: Optional[dict] = None

    def record(self, *, returncode: int, runtime_seconds: float, command_string: str) -> None:
        with self._lock:
            self._total += 1
            if returncode == 0:
                self._success += 1
            else:
                self._failure += 1
            self._total_runtime += runtime_seconds
            self._last_run = {
                "returncode":      returncode,
                "runtime_seconds": round(runtime_seconds, 3),
                "command_string":  command_string,
                "finished_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

    def snapshot(self) -> dict:
        with self._lock:
            success_rate = (self._success / self._total) if self._total else 0.0
            avg_runtime = (self._total_runtime / self._total) if self._total else 0.0
            return {
                "total_runs":          self._total,
                "success_count":       self._success,
                "failure_count":       self._failure,
                "success_rate":        round(success_rate, 4),
                "avg_runtime_seconds": round(avg_runtime, 3),
                "last_run":            self._last_run,
            }


_stats = RunStats()


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/version")
def version():
    return jsonify({
        "sidecar":   "genoa-splat-sidecar",
        "splat_bin": SPLAT_BIN,
        "workdir":   str(WORKDIR),
    })


@app.get("/api/v1/stats")
def stats():
    return jsonify(_stats.snapshot())


@app.post("/api/v1/splat/run")
def splat_run():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"invalid json: {exc}"}), 400

    command = _build_command(payload or {})
    if isinstance(command, str):
        return jsonify({"error": command}), 400

    timeout = int((payload or {}).get("timeout_seconds", 120))
    command_string = shlex.join(command)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(WORKDIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        # Record the timeout as a failure so dashboards reflect it.
        _stats.record(returncode=-1, runtime_seconds=elapsed, command_string=command_string)
        return jsonify({"error": "splat run timed out"}), 408

    elapsed = time.monotonic() - started
    _stats.record(
        returncode=result.returncode,
        runtime_seconds=elapsed,
        command_string=command_string,
    )

    return jsonify(
        {
            "command":        command,
            "command_string": command_string,
            "returncode":     result.returncode,
            "stdout":         result.stdout,
            "stderr":         result.stderr,
        }
    )


def _build_command(payload: dict) -> Union[list, str]:
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


if __name__ == "__main__":
    # Local dev fallback.  Production runs via gunicorn:
    #   gunicorn --bind 0.0.0.0:${PORT:-8080} genoa_sidecar:app
    app.run(host=HOST, port=PORT)
