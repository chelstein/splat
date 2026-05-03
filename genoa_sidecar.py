#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import Flask, jsonify, request, send_file

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", os.getenv("GENOA_PORT", "8080")))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
DASHBOARD = Path(__file__).with_name("dashboard.html")


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


app = Flask(__name__)
stats = RunStats()
stats_lock = threading.Lock()


def build_splat_command(payload: dict) -> list[str] | str:
    tx_qth = payload.get("tx_qth")
    if not tx_qth:
        return "tx_qth is required"
    cmd = [SPLAT_BIN, "-t", str(tx_qth)]
    if payload.get("rx_qth"):
        cmd.extend(["-r", str(payload["rx_qth"])])
    if payload.get("output_base"):
        cmd.extend(["-o", str(payload["output_base"])])
    for flag in payload.get("flags", []):
        cmd.append(str(flag))
    return cmd


def update_stats(returncode: int, runtime_seconds: float) -> None:
    with stats_lock:
        prev_sum = stats.avg_runtime_seconds * stats.total_runs
        stats.total_runs += 1
        if returncode == 0:
            stats.success_runs += 1
        else:
            stats.failed_runs += 1
        stats.avg_runtime_seconds = (prev_sum + runtime_seconds) / stats.total_runs
        stats.last_run_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stats.last_returncode = returncode


@app.get("/")
@app.get("/dashboard")
def dashboard():
    if not DASHBOARD.exists():
        return jsonify({"error": "dashboard missing"}), 404
    return send_file(DASHBOARD)


@app.get("/healthz")
@app.get("/readyz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/api/v1/stats")
def get_stats():
    with stats_lock:
        body = asdict(stats)
        body["success_rate"] = stats.success_rate
    return jsonify(body)


@app.post("/api/v1/splat/run")
def run_splat():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "request body is required"}), 400

    command = build_splat_command(payload)
    if isinstance(command, str):
        return jsonify({"error": command}), 400

    timeout_seconds = int(payload.get("timeout_seconds", 120))
    start = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "splat run timed out"}), 408

    runtime_seconds = time.time() - start
    update_stats(result.returncode, runtime_seconds)

    return jsonify(
        {
            "command": command,
            "command_string": shlex.join(command),
            "returncode": result.returncode,
            "runtime_seconds": runtime_seconds,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )


if __name__ == "__main__":
    WORKDIR.mkdir(parents=True, exist_ok=True)
    app.run(host=HOST, port=PORT)
