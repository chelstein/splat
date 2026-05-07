#!/usr/bin/env python3
"""Genoa sidecar HTTP service for running SPLAT! as an API endpoint.

Flask + gunicorn version (so the DigitalOcean App Platform CMD
`gunicorn genoa_sidecar:app` can bind directly).  Routes:

  GET  /healthz              -> {"status": "ok"}
  GET  /version              -> sidecar metadata + git SHA + SPLAT version
  GET  /api/v1/stats         -> per-worker run counters (see RunStats)
  POST /api/v1/splat/run     -> runs ./splat with the given args

The SPLAT binary is invoked via subprocess against `WORKDIR`.  The
container is expected to have already built `./splat` (Dockerfile runs
`./configure && ./build && ./install`).

Lifecycle: a background thread sweeps stale files out of WORKDIR every
GENOA_SWEEP_INTERVAL_SECONDS (default 3600). Files older than
GENOA_RETENTION_HOURS (default 24) are deleted. Set retention to 0 to
disable the sweeper.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from flask import Flask, jsonify, request

log = logging.getLogger("genoa-sidecar")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

GIT_COMMIT_SHA = os.getenv("GIT_COMMIT_SHA", "unknown")
BUILD_TIME     = os.getenv("BUILD_TIME", "unknown")
RETENTION_HOURS = float(os.getenv("GENOA_RETENTION_HOURS", "24"))
SWEEP_INTERVAL_SECONDS = float(os.getenv("GENOA_SWEEP_INTERVAL_SECONDS", "3600"))

app = Flask("genoa-sidecar")


def _detect_splat_version() -> str:
    """Try to extract the SPLAT version banner.

    SPLAT prints its version on the first line of `splat -h`. If the
    binary is missing, unrunnable, or formatted unexpectedly, return
    "unknown" — the sidecar must still come up cleanly.
    """
    try:
        result = subprocess.run(
            [SPLAT_BIN, "-h"],
            cwd=str(WORKDIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    blob = (result.stdout or "") + "\n" + (result.stderr or "")
    for line in blob.splitlines():
        line = line.strip()
        if line.lower().startswith("splat") and any(c.isdigit() for c in line):
            return line
    return "unknown"


SPLAT_VERSION = _detect_splat_version()


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


def sweep_workdir(workdir: Path, retention_hours: float, *, now: Optional[float] = None) -> int:
    """Delete files in `workdir` whose mtime is older than retention_hours.

    Returns the number of files deleted. Subdirectories are recursed but
    not themselves deleted. The sample_data/ tree (shipped with the
    image) is left alone.

    Pass `now` (epoch seconds) to make this deterministic in tests.
    """
    if retention_hours <= 0:
        return 0
    cutoff = (now if now is not None else time.time()) - retention_hours * 3600.0
    deleted = 0
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        # Skip files under sample_data — those are reference fixtures, not run output.
        try:
            rel = path.relative_to(workdir)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "sample_data":
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except FileNotFoundError:
            continue  # raced with another sweeper / caller
        except OSError as exc:
            log.warning("sweep failed to delete %s: %s", path, exc)
    return deleted


def _sweeper_loop() -> None:
    while True:
        try:
            n = sweep_workdir(WORKDIR, RETENTION_HOURS)
            if n:
                log.info("sweeper deleted %d stale file(s) from %s", n, WORKDIR)
        except Exception as exc:  # noqa: BLE001
            log.exception("sweeper iteration failed: %s", exc)
        time.sleep(SWEEP_INTERVAL_SECONDS)


def _start_sweeper() -> None:
    """Start the workdir sweeper as a daemon thread, once per process.

    Idempotent (gunicorn workers each fork the module, so this runs once
    per worker). No-op when retention is 0.
    """
    if RETENTION_HOURS <= 0:
        log.info("workdir sweeper disabled (GENOA_RETENTION_HOURS=0)")
        return
    t = threading.Thread(target=_sweeper_loop, name="genoa-sweeper", daemon=True)
    t.start()
    log.info(
        "workdir sweeper started (retention=%.1fh interval=%.0fs workdir=%s)",
        RETENTION_HOURS, SWEEP_INTERVAL_SECONDS, WORKDIR,
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/version")
def version():
    return jsonify({
        "sidecar":        "genoa-splat-sidecar",
        "git_commit_sha": GIT_COMMIT_SHA,
        "build_time":     BUILD_TIME,
        "splat_bin":      SPLAT_BIN,
        "splat_version":  SPLAT_VERSION,
        "workdir":        str(WORKDIR),
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


# Start the sweeper when imported under gunicorn (and when run directly).
# Safe to import-time: it's a daemon thread that no-ops if retention=0.
_start_sweeper()


if __name__ == "__main__":
    # Local dev fallback.  Production runs via gunicorn:
    #   gunicorn --bind 0.0.0.0:${PORT:-8080} genoa_sidecar:app
    app.run(host=HOST, port=PORT)
