"""POST /api/v1/sdf/convert/srtm/<input_name> - DEM to SDF tile pipeline.

Genoa side wants a way to provision SPLAT terrain tiles without
shelling into the sidecar.  The existing POST /api/v1/sdf/<name>
already accepts pre-built .sdf bytes; this endpoint closes the gap
for the much more common case where the caller has raw SRTM .hgt
data (e.g., freshly fetched from USGS S3) and needs the sidecar to
run srtm2sdf for it.

Flow per request:
  1. Validate the input name matches the SRTM-3 / SRTM-1 convention
     (NLLLELLL.hgt or .bil, optionally .zip-wrapped) so srtm2sdf's
     filename-driven coord parser doesn't choke.
  2. Materialise an ephemeral run dir under WORKDIR/convert-<uuid>/.
  3. Write the body bytes to <run_dir>/<input_name>.  If .zip,
     extract the inner .hgt / .bil and switch the working filename.
  4. Run /app/utils/srtm2sdf <input_name> with cwd=<run_dir>.  It
     writes the resulting <lat_lo>:<lat_hi>:<lon_lo>:<lon_hi>.sdf
     into the run dir.
  5. Move the .sdf to WORKDIR/sdf/ (overwriting any existing tile
     of the same name - operators rely on this to refresh tiles).
  6. Clean up the run dir; return { name, size_bytes, url } 201.

Auth-gated via the host module's _is_authorized.  Body size capped
at the same MAX_CONTENT_LENGTH as the existing /api/v1/sdf upload.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

bp = Blueprint("sdf_converter", __name__)
log = logging.getLogger("genoa-sidecar.sdf-converter")

_CFG: Dict[str, Any] = {}


def attach(*, workdir: Path, sdf_dir: Path, utils_dir: Path,
           is_authorized, run_dir_prefix: str = "convert-",
           timeout_seconds: int = 120) -> None:
    _CFG.update({
        "workdir":         workdir,
        "sdf_dir":         sdf_dir,
        "utils_dir":       utils_dir,
        "is_authorized":   is_authorized,
        "run_dir_prefix":  run_dir_prefix,
        "timeout_seconds": int(timeout_seconds),
    })


# Accepts both 3-arc-sec (NLLLELLL.hgt) and the .bil variant for the
# USGS Seamless server output.  Also accepts the .zip-wrapped variant
# served by most public SRTM mirrors (ESA STEP, USGS, NASA EarthData)
# so the JS provisioner can pass straight-from-mirror bytes through.
# Filename pattern is enforced because srtm2sdf parses the coords
# directly out of it; a sloppy name here yields nonsense .sdf tile
# names.
_SRTM_NAME_RE = re.compile(r"^[NSns][0-9]{2}[EWew][0-9]{3}\.(hgt|bil)(\.zip)?$")

# SDF filenames as written by srtm2sdf are
# <lat_lo>:<lat_hi>:<lon_lo>:<lon_hi>.sdf - strip anything that doesn't
# match before promoting into WORKDIR/sdf/.
_SDF_OUT_RE = re.compile(r"^-?\d+:-?\d+:-?\d+:-?\d+\.sdf$")


def _validate_input_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return "input_name is required"
    if "/" in name or "\\" in name or ".." in name:
        return "input_name must not contain path separators or '..'"
    if not _SRTM_NAME_RE.match(name):
        return ("input_name must match SRTM convention "
                "(e.g. N40W074.hgt, N40W074.hgt.zip or S22E018.bil)")
    return None


def _normalise_input_name(name: str) -> str:
    """srtm2sdf is case-sensitive on the directional letters.  Force
    upper for N/S/E/W so callers can hand us either case."""
    return name[0].upper() + name[1:3] + name[3].upper() + name[4:]


@bp.post("/api/v1/sdf/convert/srtm/<input_name>")
def convert_srtm(input_name: str):
    if not _CFG:
        return jsonify({"error": "sdf-converter not configured"}), 500
    if not _CFG["is_authorized"](request):
        return jsonify({"error": "unauthorized"}), 401

    err = _validate_input_name(input_name)
    if err:
        return jsonify({"error": err}), 400
    input_name = _normalise_input_name(input_name)

    # Body bytes: accept either multipart 'file' or a raw octet-stream
    # body so the JS side can post via the same fetch() it uses for
    # POST /api/v1/sdf/<name>.
    upload = request.files.get("file") if request.files else None
    if upload is not None:
        body_bytes = upload.read()
    else:
        body_bytes = request.get_data(cache=False)
    if not body_bytes:
        return jsonify({"error": "request body is empty"}), 400

    workdir   = _CFG["workdir"]
    sdf_dir   = _CFG["sdf_dir"]
    utils_dir = _CFG["utils_dir"]
    timeout   = _CFG["timeout_seconds"]

    srtm2sdf = utils_dir / "srtm2sdf"
    if not srtm2sdf.is_file():
        return jsonify({
            "error": f"srtm2sdf not found at {srtm2sdf}",
            "hint":  "image was built without ./build all; rebuild with the current Dockerfile",
        }), 500

    run_dir = (workdir / f"{_CFG['run_dir_prefix']}{uuid.uuid4().hex[:12]}").resolve()
    try:
        run_dir.relative_to(workdir)
    except ValueError:
        return jsonify({"error": "run dir escaped WORKDIR"}), 500
    run_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    stdout, stderr, returncode = "", "", -1
    try:
        input_path = run_dir / input_name
        input_path.write_bytes(body_bytes)

        # If the caller posted a .hgt.zip / .bil.zip (the format most
        # public SRTM mirrors serve), unzip in place and switch the
        # input_name to the unzipped file for srtm2sdf.  Single-file
        # zips only - error 422 if the archive contains anything else.
        if input_name.endswith(".zip"):
            import zipfile
            try:
                with zipfile.ZipFile(input_path) as zf:
                    members = [m for m in zf.namelist() if not m.endswith("/")]
                    if not members:
                        return jsonify({"error": "zip archive is empty"}), 422
                    inner = members[0]
                    inner_safe = Path(inner).name  # strip any path component
                    if not re.match(r"^[NSns][0-9]{2}[EWew][0-9]{3}\.(hgt|bil)$", inner_safe):
                        return jsonify({
                            "error": f"zip member {inner_safe!r} is not a valid SRTM tile",
                        }), 422
                    target = run_dir / inner_safe
                    with zf.open(inner) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            except zipfile.BadZipFile:
                return jsonify({"error": "input is not a valid zip archive"}), 422
            input_name = inner_safe   # srtm2sdf runs against the unzipped file

        # srtm2sdf expects the input file as a positional arg; -d /dev/null
        # disables the optional usgs2sdf-replacement step (which would
        # need its own staging directory).  Operator can pre-stage that
        # via the existing /api/v1/sdf upload route if they need it.
        cmd = [str(srtm2sdf), "-d", "/dev/null", input_name]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(run_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                input="\n",   # srtm2sdf may prompt for outlier handling on stdin
            )
        except subprocess.TimeoutExpired:
            return jsonify({"error": "srtm2sdf timed out", "timeout_seconds": timeout}), 408
        except FileNotFoundError:
            return jsonify({
                "error": f"srtm2sdf not found at {srtm2sdf}",
                "hint":  "image was built without ./build all; rebuild with the current Dockerfile",
            }), 500

        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode

        if returncode != 0:
            return jsonify({
                "error":      "srtm2sdf failed",
                "returncode": returncode,
                "stdout":     stdout[:2000],
                "stderr":     stderr[:2000],
            }), 422

        # Locate the produced .sdf.  srtm2sdf writes one .sdf into the
        # cwd; if anything else snuck through, we pick the largest .sdf
        # by mtime as a defensive fallback.
        produced: List[Path] = sorted(
            (p for p in run_dir.iterdir()
             if p.is_file() and p.suffix == ".sdf" and _SDF_OUT_RE.match(p.name)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not produced:
            return jsonify({
                "error":  "srtm2sdf produced no .sdf file",
                "stdout": stdout[:2000],
                "stderr": stderr[:2000],
            }), 422

        out_sdf = produced[0]
        sdf_dir.mkdir(parents=True, exist_ok=True)
        target = (sdf_dir / out_sdf.name).resolve()
        try:
            target.relative_to(sdf_dir)
        except ValueError:
            return jsonify({"error": "produced .sdf name resolves outside SDF dir"}), 500
        shutil.move(str(out_sdf), str(target))

        stat = target.stat()
        elapsed = time.monotonic() - started
        return jsonify({
            "name":            target.name,
            "input_name":      input_name,
            "size_bytes":      stat.st_size,
            "modified_at":     datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "url":             f"/api/v1/artifacts/sdf/{target.name}",
            "runtime_seconds": round(elapsed, 3),
            "returncode":      returncode,
            "stdout":          stdout[:2000],
        }), 201
    finally:
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
