#!/usr/bin/env python3
"""Genoa sidecar HTTP service for running SPLAT! as an API endpoint.

Flask + gunicorn version (so the DigitalOcean App Platform CMD
`gunicorn genoa_sidecar:app` can bind directly).  Routes:

  GET    /healthz                       -> {"status": "ok"}                 (always open)
  GET    /version                       -> sidecar metadata + git SHA + SPLAT version  (always open)
  GET    /api/v1/stats                  -> per-worker run counters           (always open)
  POST   /api/v1/splat/run              -> runs ./splat with the given args  (auth-gated when GENOA_API_TOKEN is set)
  POST   /api/v1/splat/run-inline       -> JSON-in coverage sweep (no on-disk QTH) (auth-gated)
  POST   /api/v1/sdf/convert/srtm/<n>   -> run srtm2sdf on uploaded .hgt and stage the .sdf (auth-gated)
  POST   /api/v1/itm/p2p                -> point_to_point_ITM thunk for splat-vs-JS bake-off (auth-gated)
  GET    /api/v1/artifacts              -> list files in WORKDIR              (auth-gated)
  GET    /api/v1/artifacts/<path>       -> fetch a file from WORKDIR         (auth-gated)
  GET    /api/v1/sdf                    -> list SPLAT terrain (.sdf/.sdz) tiles  (auth-gated)
  POST   /api/v1/sdf/<name>             -> upload an .sdf/.sdz tile           (auth-gated)
  DELETE /api/v1/sdf/<name>             -> remove an .sdf/.sdz tile           (auth-gated)

Auth: opt-in.  If GENOA_API_TOKEN is unset, the run/artifact/sdf
endpoints are open (backward compatible).  If set, callers must send
  Authorization: Bearer <token>
or receive 401.  Read-only health endpoints (/healthz, /version,
/api/v1/stats) remain unauthenticated so platform health checks and
dashboards work without a token.

Path confinement: regardless of auth, every path-like field on the run
payload (`tx_qth`, `rx_qth`, `output_base`) and the artifact path must
resolve INSIDE WORKDIR after Path.resolve(); otherwise the request is
rejected 400/404.  SDF tile names must be a single filename (no path
separators, no `..`) and must end with `.sdf` or `.sdz`.

SDF directory layout: tiles live under WORKDIR/sdf/.  The sweeper
exempts that subdir so terrain data persists.  Callers running SPLAT
should pass `-d sdf` in `flags` so SPLAT finds the tiles.

The SPLAT binary is invoked via subprocess against `WORKDIR`.  The
container is expected to have already built `./splat` (Dockerfile runs
`./configure && ./build && ./install`).

Lifecycle: a background thread sweeps stale files out of WORKDIR every
GENOA_SWEEP_INTERVAL_SECONDS (default 3600). Files older than
GENOA_RETENTION_HOURS (default 24) are deleted, EXCEPT for files under
sample_data/ (image fixtures) and sdf/ (terrain tiles).  Set retention
to 0 to disable the sweeper.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from flask import Flask, jsonify, request, send_from_directory

log = logging.getLogger("genoa-sidecar")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

HOST = os.getenv("GENOA_HOST", "0.0.0.0")
PORT = int(os.getenv("GENOA_PORT", "8080"))
SPLAT_BIN = os.getenv("SPLAT_BIN", "./splat")
WORKDIR = Path(os.getenv("SPLAT_WORKDIR", ".")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

SDF_DIR_NAME = "sdf"
SDF_DIR = (WORKDIR / SDF_DIR_NAME).resolve()
SDF_DIR.mkdir(parents=True, exist_ok=True)

# Sweeper-exempt top-level directories.  Anything under these is
# preserved regardless of mtime (sample_data/ are shipped fixtures;
# sdf/ is operator-uploaded terrain).
SWEEPER_EXEMPT_DIRS = ("sample_data", SDF_DIR_NAME)

GIT_COMMIT_SHA = os.getenv("GIT_COMMIT_SHA", "unknown")
BUILD_TIME     = os.getenv("BUILD_TIME", "unknown")
RETENTION_HOURS = float(os.getenv("GENOA_RETENTION_HOURS", "24"))
SWEEP_INTERVAL_SECONDS = float(os.getenv("GENOA_SWEEP_INTERVAL_SECONDS", "3600"))
SDF_MAX_UPLOAD_BYTES = int(os.getenv("GENOA_SDF_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))

app = Flask("genoa-sidecar")
app.config["MAX_CONTENT_LENGTH"] = SDF_MAX_UPLOAD_BYTES


def _detect_splat_version() -> str:
    """Try to extract the SPLAT version banner.  Returns 'unknown' on failure.

    SPLAT prints its banner only when invoked with NO arguments
    (argc==1 in splat.cpp); the `-h` flag is misinterpreted as
    "terrain-height graph filename" and errors out.  The banner
    format is `--==[ SPLAT! v1.4.2 Available Options... ]==--`."""
    try:
        result = subprocess.run(
            [SPLAT_BIN],
            cwd=str(WORKDIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    blob = (result.stdout or "") + "\n" + (result.stderr or "")
    m = re.search(r"SPLAT!?\s*(?:HD\s+)?v\s*([0-9][0-9A-Za-z.\-]*)", blob)
    if m:
        return f"SPLAT! v{m.group(1)}"
    return "unknown"


SPLAT_VERSION = _detect_splat_version()


class RunStats:
    """In-memory aggregator for /api/v1/splat/run invocations (per worker)."""

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


# ---------- Auth ----------

def _configured_token() -> Optional[str]:
    """Return the configured bearer token, or None if auth is disabled."""
    tok = (os.getenv("GENOA_API_TOKEN") or "").strip()
    return tok or None


def _is_authorized(req) -> bool:
    """True when auth is disabled OR when the request carries a valid bearer."""
    token = _configured_token()
    if token is None:
        return True
    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    presented = header[len(prefix):].strip()
    return secrets.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))


# ---------- Path confinement ----------

def _path_inside_workdir(value: Optional[str]) -> bool:
    """True if `value` is empty / None, or resolves inside WORKDIR."""
    if not value:
        return True
    try:
        resolved = (WORKDIR / str(value)).resolve()
        resolved.relative_to(WORKDIR)
    except (ValueError, OSError):
        return False
    return True


def _flag_has_traversal(flag: object) -> bool:
    """True if a `flags[]` entry tries to sneak a traversal past path checks."""
    s = str(flag)
    if ".." in s:
        return True
    if s.startswith("/"):
        return True
    return False


# ---------- SDF naming ----------

# SPLAT tile filenames are conventionally `<lat_lo>:<lat_hi>:<lon_lo>:<lon_hi>.sdf`
# with an optional `.sdz` (gzip) variant.  We accept any safe filename
# ending with .sdf or .sdz; structural validation belongs to SPLAT itself.
_SDF_NAME_RE = re.compile(r"^[A-Za-z0-9._:+\-]{1,80}\.(sdf|sdz)$")


def _validate_sdf_name(name: Optional[str]) -> Optional[str]:
    """Returns None if `name` is a safe SDF filename, else an error string."""
    if not name:
        return "name is required"
    if "/" in name or "\\" in name:
        return "name must not contain path separators"
    if ".." in name:
        return "name must not contain '..'"
    if not _SDF_NAME_RE.match(name):
        return "name must match [A-Za-z0-9._:+\\-]{1,80}\\.(sdf|sdz)"
    return None


def sweep_workdir(workdir: Path, retention_hours: float, *, now: Optional[float] = None) -> int:
    """Delete files in `workdir` whose mtime is older than retention_hours.

    Files under the SWEEPER_EXEMPT_DIRS (sample_data/, sdf/) are
    preserved regardless of mtime - they're long-lived terrain data
    and image fixtures, not run output.
    """
    if retention_hours <= 0:
        return 0
    cutoff = (now if now is not None else time.time()) - retention_hours * 3600.0
    deleted = 0
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(workdir)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in SWEEPER_EXEMPT_DIRS:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except FileNotFoundError:
            continue
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
    if RETENTION_HOURS <= 0:
        log.info("workdir sweeper disabled (GENOA_RETENTION_HOURS=0)")
        return
    t = threading.Thread(target=_sweeper_loop, name="genoa-sweeper", daemon=True)
    t.start()
    log.info(
        "workdir sweeper started (retention=%.1fh interval=%.0fs workdir=%s exempt=%s)",
        RETENTION_HOURS, SWEEP_INTERVAL_SECONDS, WORKDIR, SWEEPER_EXEMPT_DIRS,
    )


# ---------- Routes ----------

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
        "sdf_dir":        SDF_DIR_NAME,
        "auth_required":  _configured_token() is not None,
        # DO App Platform auto-injects APP_DEPLOYMENT_ID and APP_DOMAIN at
        # runtime.  These give an unambiguous "what's actually running" signal
        # without the manual upkeep that GIT_COMMIT_SHA + BUILD_TIME require
        # (those are baked at build time and have to be re-stamped on each
        # apps-update).  Empty string when running outside App Platform.
        "deployment_id":  os.getenv("APP_DEPLOYMENT_ID", ""),
        "app_domain":     os.getenv("APP_DOMAIN", ""),
    })


@app.get("/api/v1/stats")
def stats():
    return jsonify(_stats.snapshot())


@app.post("/api/v1/splat/run")
def splat_run():
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401

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
    except FileNotFoundError:
        elapsed = time.monotonic() - started
        _stats.record(returncode=-1, runtime_seconds=elapsed, command_string=command_string)
        return jsonify({
            "error": f"splat binary not found at {SPLAT_BIN}",
            "hint":  "image was built without ./build all; rebuild with the current Dockerfile",
        }), 500

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


@app.get("/api/v1/artifacts")
def artifacts_list():
    """List files in WORKDIR (excluding sample_data/ and sdf/), newest first."""
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    items = []
    for path in WORKDIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(WORKDIR)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in SWEEPER_EXEMPT_DIRS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel_posix = rel.as_posix()
        items.append({
            "path":        rel_posix,
            "size_bytes":  stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "url":         f"/api/v1/artifacts/{rel_posix}",
        })
    items.sort(key=lambda r: r["modified_at"], reverse=True)
    return jsonify({
        "workdir":   str(WORKDIR),
        "count":     len(items),
        "artifacts": items,
    })


@app.get("/api/v1/artifacts/<path:filename>")
def artifacts_get(filename):
    """Fetch a file from WORKDIR.  Path-confined.  Auth-gated."""
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401
    if not _path_inside_workdir(filename):
        return jsonify({"error": "path resolves outside WORKDIR"}), 400
    target = (WORKDIR / filename).resolve()
    if not target.is_file():
        return jsonify({"error": "not found"}), 404
    return send_from_directory(str(WORKDIR), filename, as_attachment=False)


# ---------- SDF terrain tile lifecycle ----------

@app.get("/api/v1/sdf")
def sdf_list():
    """List SPLAT terrain tiles in WORKDIR/sdf/."""
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    tiles = []
    if SDF_DIR.is_dir():
        for path in SDF_DIR.iterdir():
            if not path.is_file():
                continue
            if path.suffix not in (".sdf", ".sdz"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            tiles.append({
                "name":        path.name,
                "size_bytes":  stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                "url":         f"/api/v1/artifacts/{SDF_DIR_NAME}/{path.name}",
            })
    tiles.sort(key=lambda t: t["name"])
    return jsonify({
        "sdf_dir":              SDF_DIR_NAME,
        "max_upload_bytes":     SDF_MAX_UPLOAD_BYTES,
        "count":                len(tiles),
        "tiles":                tiles,
    })


@app.post("/api/v1/sdf/<name>")
def sdf_upload(name):
    """Upload an .sdf or .sdz tile.

    Body may be:
      - raw application/octet-stream bytes (no multipart wrapping), OR
      - multipart/form-data with a single 'file' field.

    Overwrites any existing tile of the same name (operators rely on
    this to refresh stale terrain).  Auth-gated when GENOA_API_TOKEN
    is set.  Body size is capped by GENOA_SDF_MAX_UPLOAD_BYTES (default
    64 MiB); exceeding it returns 413.
    """
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    err = _validate_sdf_name(name)
    if err:
        return jsonify({"error": err}), 400

    SDF_DIR.mkdir(parents=True, exist_ok=True)
    target = (SDF_DIR / name).resolve()
    # Defense in depth: confirm target is inside SDF_DIR even after
    # resolve()/symlink chasing.  _validate_sdf_name should have
    # blocked any traversal already, but check again.
    try:
        target.relative_to(SDF_DIR)
    except ValueError:
        return jsonify({"error": "name resolves outside SDF dir"}), 400

    # Accept either a multipart upload (single 'file' field) or a raw body.
    upload = request.files.get("file") if request.files else None
    if upload is not None:
        upload.save(str(target))
    else:
        data = request.get_data(cache=False)
        if not data:
            return jsonify({"error": "request body is empty"}), 400
        target.write_bytes(data)

    stat = target.stat()
    return jsonify({
        "name":        name,
        "size_bytes":  stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "url":         f"/api/v1/artifacts/{SDF_DIR_NAME}/{name}",
    }), 201


@app.delete("/api/v1/sdf/<name>")
def sdf_delete(name):
    """Remove an SDF tile.  404 if missing.  Auth-gated."""
    if not _is_authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    err = _validate_sdf_name(name)
    if err:
        return jsonify({"error": err}), 400

    target = (SDF_DIR / name).resolve()
    try:
        target.relative_to(SDF_DIR)
    except ValueError:
        return jsonify({"error": "name resolves outside SDF dir"}), 400

    if not target.is_file():
        return jsonify({"error": "not found"}), 404
    target.unlink()
    return jsonify({"deleted": name})


def _build_command(payload: dict) -> Union[list, str]:
    """Validate the payload and assemble the SPLAT argv.

    Returns a list (the argv) on success.  Returns a string error
    message on validation failure; the caller turns that into a 400.
    """
    tx_qth = payload.get("tx_qth")
    if not tx_qth:
        return "tx_qth is required"

    for field in ("tx_qth", "rx_qth", "output_base"):
        value = payload.get(field)
        if not _path_inside_workdir(value):
            return f"{field} resolves outside WORKDIR"

    for flag in payload.get("flags", []):
        if _flag_has_traversal(flag):
            return f"flags entry {flag!r} contains path traversal or absolute path"

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


# Register the JSON-in inline coverage sweep endpoint.  Kept in a
# separate module so the host file stays focused on lifecycle + path
# confinement; the blueprint reuses _is_authorized so auth behaves
# identically to the rest of /api/v1/*.
import inline_runner  # noqa: E402

inline_runner.attach(
    workdir=WORKDIR,
    sdf_dir_name=SDF_DIR_NAME,
    splat_bin=SPLAT_BIN,
    is_authorized=_is_authorized,
)
app.register_blueprint(inline_runner.bp)


# Register the SRTM -> SDF converter endpoint.  Same pattern as the
# inline runner: separate module, attach() with shared lifecycle
# state, register_blueprint().  SPLAT_UTILS_DIR comes from the
# Dockerfile and points at /app/utils where ./build all puts the
# srtm2sdf / usgs2sdf binaries.
import sdf_converter  # noqa: E402

SPLAT_UTILS_DIR = Path(os.getenv("SPLAT_UTILS_DIR", "/app/utils")).resolve()

sdf_converter.attach(
    workdir=WORKDIR,
    sdf_dir=SDF_DIR,
    utils_dir=SPLAT_UTILS_DIR,
    is_authorized=_is_authorized,
)
app.register_blueprint(sdf_converter.bp)


# Register the point-to-point ITM thunk endpoint (/api/v1/itm/p2p).
# Bake-off reference for chelstein/itmlogic's JS port: takes the same
# inputs the JS pointToPoint() consumes (profile + p2p params) and
# returns the dbloss / strmode / errnum that the C++ point_to_point_ITM
# emits.  Backed by the test_p2p binary compiled by the Dockerfile
# from test_p2p.cpp.
import itm_p2p  # noqa: E402

SPLAT_TEST_P2P_BIN = Path(os.getenv("SPLAT_TEST_P2P_BIN", "/app/test_p2p")).resolve()

itm_p2p.attach(
    test_p2p_bin=SPLAT_TEST_P2P_BIN,
    is_authorized=_is_authorized,
)
app.register_blueprint(itm_p2p.bp)


_start_sweeper()


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
