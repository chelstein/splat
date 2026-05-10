"""POST /api/v1/itm/p2p  +  POST /api/v1/itm/p2p_itwom — JSON-in,
JSON-out thunks around itwom3.0.cpp's two p2p entry points.

Genoa needs numerical references for the splat-vs-JS bake-off:
  /api/v1/itm/p2p        - calls point_to_point_ITM() (NTIA v1.2.2)
                           via /app/test_p2p
  /api/v1/itm/p2p_itwom  - calls point_to_point()     (ITWOM 3.0)
                           via /app/test_p2p_itwom

Both are compiled by the Dockerfile from the matching cpp source.
Each endpoint shells out, pipes the JSON request to the binary's
stdin, parses the JSON it emits on stdout, returns it to the caller.

Auth-gated via the host module's _is_authorized.  No file persistence
(unlike inline_runner / sdf_converter); these endpoints are stateless
- the binary's stdin/stdout is the entire I/O surface.

Sample request:
  curl -s https://genoaiq.com/splat/api/v1/itm/p2p_itwom \\
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

Sample response (ITWOM):
  {
    "dbloss":  148.20,
    "errnum":  0,
    "strmode": "2_Hrzn_Diff",
    "fs":      106.43,
    "dist_m":  49900,
    "splat_version": 3.0,
    "runtime_seconds": 0.013
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

log = logging.getLogger("genoa-sidecar.itm-p2p")


# ---------------------------------------------------------------------
# Validation (shared between both endpoints - same JSON shape).
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Blueprint factory.  One factory call -> one blueprint, bound to one
# binary, exposing one endpoint route.  The factory keeps the v1.2.2
# and ITWOM versions completely independent: they have their own
# blueprint names and config slots, so registering both into the same
# Flask app does not collide.
# ---------------------------------------------------------------------

def make_blueprint(*, name: str, route_path: str, binary_path: Path,
                   is_authorized, timeout_seconds: int = 30) -> Blueprint:
    bp = Blueprint(name, __name__)
    cfg = {
        "binary_path":     Path(binary_path),
        "is_authorized":   is_authorized,
        "timeout_seconds": int(timeout_seconds),
    }

    @bp.post(route_path)
    def _handler():  # noqa: D401  (route handler; signature is what Flask sees)
        if not cfg["is_authorized"](request):
            return jsonify({"error": "unauthorized"}), 401

        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"invalid json: {exc}"}), 400

        err, params = _validate_payload(payload)
        if err:
            return jsonify({"error": err}), 400
        assert params is not None

        bin_path = cfg["binary_path"]
        if not bin_path.is_file():
            return jsonify({
                "error": f"binary not found at {bin_path}",
                "hint":  "image was built without the matching test_p2p* compile step; rebuild with the current Dockerfile",
            }), 500

        body_bytes = json.dumps(params).encode("utf-8")
        started    = time.monotonic()
        try:
            proc = subprocess.run(
                [str(bin_path)],
                input=body_bytes,
                capture_output=True,
                timeout=cfg["timeout_seconds"],
                check=False,
            )
        except subprocess.TimeoutExpired:
            return jsonify({"error": f"{bin_path.name} timed out"}), 408
        except FileNotFoundError:
            return jsonify({
                "error": f"binary not found at {bin_path}",
                "hint":  "image was built without the matching test_p2p* compile step; rebuild with the current Dockerfile",
            }), 500

        elapsed = time.monotonic() - started

        if proc.returncode != 0:
            return jsonify({
                "error":      f"{bin_path.name} failed",
                "returncode": proc.returncode,
                "stdout":     proc.stdout.decode("utf-8", "replace")[:2000],
                "stderr":     proc.stderr.decode("utf-8", "replace")[:2000],
            }), 422

        try:
            out = json.loads(proc.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return jsonify({
                "error":  f"{bin_path.name} output was not valid JSON: {exc}",
                "stdout": proc.stdout.decode("utf-8", "replace")[:2000],
            }), 502

        out["runtime_seconds"] = round(elapsed, 4)
        return jsonify(out)

    return bp


# ---------------------------------------------------------------------
# Backward-compat shims for the existing /api/v1/itm/p2p (v1.2.2) wiring
# in genoa_sidecar.py.  Keeps `from itm_p2p import bp, attach` working
# without forcing a parallel sidecar edit.  ITWOM gets its own factory
# call in genoa_sidecar.py.
# ---------------------------------------------------------------------

_legacy_cfg: Dict[str, Any] = {}


def attach(*, test_p2p_bin: Path, is_authorized,
           timeout_seconds: int = 30) -> None:
    """Legacy attach for the v1.2.2 endpoint.  Constructs the blueprint
    lazily so the import-time `bp` reference still works.
    """
    _legacy_cfg.update({
        "test_p2p_bin":    test_p2p_bin,
        "is_authorized":   is_authorized,
        "timeout_seconds": int(timeout_seconds),
    })
    # Patch the module-level `bp` so `app.register_blueprint(itm_p2p.bp)`
    # picks up the newly-built one.
    globals()["bp"] = make_blueprint(
        name="itm_p2p",
        route_path="/api/v1/itm/p2p",
        binary_path=test_p2p_bin,
        is_authorized=is_authorized,
        timeout_seconds=timeout_seconds,
    )


# Placeholder `bp` so `from itm_p2p import bp` succeeds before attach().
# attach() replaces this with the real blueprint above.
bp = Blueprint("itm_p2p_placeholder", __name__)
