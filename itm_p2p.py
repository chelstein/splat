"""POST /api/v1/itm/p2p - JSON-in, JSON-out point_to_point_ITM thunk.

Genoa needs a numerical reference for the splat-vs-JS bake-off.  This
endpoint shells out to /app/test_p2p (compiled by the Dockerfile from
test_p2p.cpp; calls the same point_to_point_ITM that splat's site
report uses) and returns the JSON it prints.

Flow per request:
  1. Validate the JSON payload (profile array + scalar params).
  2. Spawn /app/test_p2p, pipe the request body to its stdin.
  3. Parse the JSON it emits on stdout, return it to the caller.

Auth-gated via the host module's _is_authorized.  No file persistence
(unlike inline_runner / sdf_converter); this endpoint is stateless -
the binary's stdin/stdout is the entire I/O surface.

Sample request:
  curl -s https://genoaiq.com/splat/api/v1/itm/p2p \\
    -H 'Authorization: Bearer <token>' \\
    -H 'content-type: application/json' \\
    -d '{
      "profile":      [499, 100, 0, 0, 0, ...],
      "tx_height_m":  30,
      "rx_height_m":  10,
      "frequency_mhz": 100,
      "conf": 0.5, "rel": 0.5,
      "radio_climate": 5, "pol": 1
    }'

Sample response:
  {
    "dbloss":  146.51,
    "errnum":  0,
    "strmode": "Double Horizon, Diffraction Dominant",
    "fs":      106.43,
    "dist_m":  49900,
    "splat_version": 3.0,
    "runtime_seconds": 0.012
  }
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

bp = Blueprint("itm_p2p", __name__)
log = logging.getLogger("genoa-sidecar.itm-p2p")

_CFG: Dict[str, Any] = {}


def attach(*, test_p2p_bin: Path, is_authorized,
           timeout_seconds: int = 30) -> None:
    _CFG.update({
        "test_p2p_bin":    test_p2p_bin,
        "is_authorized":   is_authorized,
        "timeout_seconds": int(timeout_seconds),
    })


_REQUIRED = ("profile", "tx_height_m", "rx_height_m", "frequency_mhz")


def _validate_payload(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    for k in _REQUIRED:
        if k not in payload or payload[k] in (None, ""):
            return f"{k} is required", None

    profile = payload["profile"]
    if not isinstance(profile, list) or len(profile) < 3:
        return "profile must be an array of at least 3 numbers", None
    try:
        np = int(profile[0])
        xi = float(profile[1])
    except (TypeError, ValueError):
        return "profile[0] (np) and profile[1] (xi) must be numeric", None
    if np < 1 or np > 100_000:
        return f"profile np ({np}) out of range [1..100000]", None
    if xi <= 0 or xi > 10_000:
        return f"profile xi ({xi}) out of range (0..10000 m)", None
    if len(profile) != np + 3:
        return f"profile length {len(profile)} != np+3 ({np + 3})", None

    try:
        fmhz = float(payload["frequency_mhz"])
        tht  = float(payload["tx_height_m"])
        rht  = float(payload["rx_height_m"])
    except (TypeError, ValueError):
        return "frequency_mhz / tx_height_m / rx_height_m must be numeric", None
    if fmhz <= 0 or fmhz > 100_000:
        return "frequency_mhz out of range", None
    if tht < 0 or tht > 10_000 or rht < 0 or rht > 10_000:
        return "tx_height_m / rx_height_m out of range (0..10000 m)", None

    # The optional knobs all have C++ defaults so missing keys are fine.
    return None, payload


@bp.post("/api/v1/itm/p2p")
def itm_p2p():
    if not _CFG:
        return jsonify({"error": "itm-p2p not configured"}), 500
    if not _CFG["is_authorized"](request):
        return jsonify({"error": "unauthorized"}), 401

    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"invalid json: {exc}"}), 400

    err, params = _validate_payload(payload)
    if err:
        return jsonify({"error": err}), 400
    assert params is not None

    bin_path = _CFG["test_p2p_bin"]
    if not bin_path.is_file():
        return jsonify({
            "error": f"test_p2p binary not found at {bin_path}",
            "hint":  "image was built without the test_p2p compile step; rebuild with the current Dockerfile",
        }), 500

    body_bytes = json.dumps(params).encode("utf-8")
    started    = time.monotonic()
    try:
        proc = subprocess.run(
            [str(bin_path)],
            input=body_bytes,
            capture_output=True,
            timeout=_CFG["timeout_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "test_p2p timed out"}), 408
    except FileNotFoundError:
        return jsonify({
            "error": f"test_p2p binary not found at {bin_path}",
            "hint":  "image was built without the test_p2p compile step; rebuild with the current Dockerfile",
        }), 500

    elapsed = time.monotonic() - started

    if proc.returncode != 0:
        return jsonify({
            "error":      "test_p2p failed",
            "returncode": proc.returncode,
            "stdout":     proc.stdout.decode("utf-8", "replace")[:2000],
            "stderr":     proc.stderr.decode("utf-8", "replace")[:2000],
        }), 422

    try:
        out = json.loads(proc.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return jsonify({
            "error":  f"test_p2p output was not valid JSON: {exc}",
            "stdout": proc.stdout.decode("utf-8", "replace")[:2000],
        }), 502

    out["runtime_seconds"] = round(elapsed, 4)
    return jsonify(out)
