#!/usr/bin/env python3
"""Genoa sidecar HTTP service for running SPLAT! as an API endpoint.

Flask + gunicorn version (so the DigitalOcean App Platform CMD
`gunicorn genoa_sidecar:app` can bind directly).  Routes and request /
response shape are unchanged from the original stdlib http.server
implementation:

  GET  /healthz              -> {"status": "ok"}
  GET  /version              -> sidecar metadata
  POST /api/v1/splat/run     -> runs ./splat with the given args

The SPLAT binary is invoked via subprocess against `WORKDIR`.  The
container is expected to have already built `./splat` (Dockerfile runs
`./configure && ./build && ./install`).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Union

from flask import Flask, jsonify, request

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

app = Flask("genoa-sidecar")


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
        return jsonify({"error": "splat run timed out"}), 408

    return jsonify(
        {
            "command":        command,
            "command_string": shlex.join(command),
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
