"""POST /api/v1/splat/run-inline — JSON-in, JSON-out coverage sweep.

Genoa side already calls this endpoint from
genoa/src/evidence/terrain/splatClient.js#predictItmCoverage; the
sidecar so far returned 404 and the JS terrain engine took over.
This module fills that gap.

Flow per request:
  1. Validate the JSON payload (frequency, ERP, polarization, sweep).
  2. Materialise an ephemeral run dir under WORKDIR/inline-<uuid>/.
  3. Render TX.qth (W-positive longitude per SPLAT convention) and
     TX.lrp (9-line ITM parameter file with operator-supplied ERP).
  4. For each bearing in 0..360 step radial_step_deg, project a
     receiver site at max_distance_km and run
       ./splat -t TX.qth -r RX_<az>.qth -metric -L <rxht_ft> -d sdf
     which produces a per-radial path-loss-vs-distance text file.
  5. Parse each radial's loss table, derive field strength when SPLAT
     didn't already, linearly interpolate the target_field_dbu
     crossing distance, and accumulate the per-radial result.
  6. Clean up the run dir; return JSON to the caller.

DEM tiles must be provisioned at WORKDIR/sdf/ ahead of time via the
existing /api/v1/sdf upload routes.  Without tiles, SPLAT falls back
to flat-Earth and the response carries terrain_used=false so the
caller knows the run is non-terrain-aware.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

bp = Blueprint("inline_runner", __name__)
log = logging.getLogger("genoa-sidecar.inline-runner")

# Filled in by attach() at registration time so the blueprint shares
# auth + WORKDIR + SPLAT_BIN with the host module without a circular
# import.
_CFG: Dict[str, Any] = {}


def attach(*, workdir: Path, sdf_dir_name: str, splat_bin: str,
           is_authorized, run_dir_prefix: str = "inline-") -> None:
    _CFG.update({
        "workdir":        workdir,
        "sdf_dir_name":   sdf_dir_name,
        "splat_bin":      splat_bin,
        "is_authorized":  is_authorized,
        "run_dir_prefix": run_dir_prefix,
    })


# ---------- math helpers -------------------------------------------

EARTH_R_KM = 6371.0088


def _destination(lat_deg: float, lon_deg_e_pos: float,
                 bearing_deg: float, distance_km: float) -> Tuple[float, float]:
    """Spherical-Earth forward formula. Inputs/outputs in mathematical
    convention (East-positive longitude)."""
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg_e_pos)
    d = distance_km / EARTH_R_KM
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d)
        + math.cos(lat1) * math.sin(d) * math.cos(br)
    )
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


# ---------- LRP / QTH rendering ------------------------------------

def _render_qth(tx_qth_content: str) -> str:
    lines = [ln.strip() for ln in tx_qth_content.splitlines() if ln.strip()]
    if len(lines) < 4:
        raise ValueError("tx_qth_content must have 4 non-empty lines")
    return "\n".join(lines[:4]) + "\n"


def _render_lrp(*, frequency_mhz: float, erp_kw: float, polarization: str,
                climate_code: int, dielectric: float = 15.0,
                conductivity: float = 0.005, bending: float = 301.0,
                fraction_situations: float = 0.50,
                fraction_time: float = 0.90) -> str:
    pol = 1 if str(polarization).upper().startswith("V") else 0
    erp_w = max(1.0, float(erp_kw) * 1000.0)
    return (
        f"{dielectric:.3f}     ; Earth dielectric constant\n"
        f"{conductivity:.3f}      ; Earth conductivity (S/m)\n"
        f"{bending:.3f}    ; Atmospheric bending constant (N-units)\n"
        f"{frequency_mhz:.3f}     ; Frequency (MHz)\n"
        f"{int(climate_code)}          ; Radio climate (5 = continental temperate)\n"
        f"{pol}          ; Polarization (0=H, 1=V)\n"
        f"{fraction_situations:.2f}       ; Fraction of situations\n"
        f"{fraction_time:.2f}       ; Fraction of time\n"
        f"{erp_w:.1f}     ; Effective Radiated Power (W)\n"
    )


def _qth_latlon_w_pos(qth_text: str) -> Tuple[float, float]:
    lines = [ln.strip() for ln in qth_text.splitlines() if ln.strip()]
    return float(lines[1]), float(lines[2])


# ---------- SPLAT loss-table parsing -------------------------------

# Expected columns in SPLAT's path_loss text dump (loose, version-tolerant):
#   <distance_km> <path_loss_dB> [<rx_dBm>] [<field_dBuV>]
_LOSS_LINE_RE = re.compile(
    r"^\s*([0-9]+\.?[0-9]*)\s+([\-0-9.]+)"
    r"(?:\s+([\-0-9.]+))?"
    r"(?:\s+([\-0-9.]+))?\s*$"
)


def _parse_loss_table(loss_path: Path) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    if not loss_path.is_file():
        return out
    try:
        text = loss_path.read_text(errors="ignore")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#%;":
            continue
        m = _LOSS_LINE_RE.match(s)
        if not m:
            continue
        try:
            d_km = float(m.group(1))
            ploss = float(m.group(2))
        except (TypeError, ValueError):
            continue
        if d_km < 0 or d_km > 5000 or ploss < 0 or ploss > 400:
            continue
        row: Dict[str, float] = {
            "distance_km": round(d_km, 3),
            "path_loss_dB": round(ploss, 2),
        }
        if m.group(3):
            try:
                row["received_dBm"] = round(float(m.group(3)), 2)
            except ValueError:
                pass
        if m.group(4):
            try:
                row["field_dbu"] = round(float(m.group(4)), 2)
            except ValueError:
                pass
        out.append(row)
    return out


def _find_loss_file(run_dir: Path, az: int) -> Optional[Path]:
    """SPLAT 1.4.2 / itwom 3.0 names this file inconsistently across
    flag combos.  Glob all txt/gp files mentioning the rx name and
    return the one with the most numeric rows."""
    rx_token = f"RX{az:03d}"
    candidates: List[Tuple[int, Path]] = []
    for p in run_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if rx_token not in name:
            continue
        if not (name.endswith(".txt") or name.endswith(".gp") or name.endswith(".dat")):
            continue
        rows = len(_parse_loss_table(p))
        if rows:
            candidates.append((rows, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# ---------- field-strength fallback --------------------------------

def _path_loss_to_field_dbu(path_loss_db: float, *,
                            frequency_mhz: float, erp_kw: float) -> float:
    """E (dBuV/m) ~= ERP (dBW) - path_loss + 20*log10(f_MHz) + 76.92.

    Used only when SPLAT didn't emit a field-strength column directly
    (older itwom variants or particular flag combos drop it)."""
    erp_dbw = 10.0 * math.log10(max(1.0, erp_kw * 1000.0))
    return erp_dbw + 20.0 * math.log10(max(1.0, frequency_mhz)) - path_loss_db + 76.92


def _interp_threshold_km(samples: List[Dict[str, float]],
                         target_dbu: float) -> Optional[float]:
    prev: Optional[Dict[str, float]] = None
    for s in samples:
        f = s.get("field_dbu")
        if f is None:
            continue
        if f < target_dbu:
            if prev is None:
                return s["distance_km"]
            d0, f0 = prev["distance_km"], prev["field_dbu"]
            d1, f1 = s["distance_km"], f
            if f0 == f1:
                return d1
            t = (f0 - target_dbu) / (f0 - f1)
            return d0 + t * (d1 - d0)
        prev = s
    return None


# ---------- payload validation -------------------------------------

_REQUIRED = ("tx_qth_content", "frequency_mhz", "erp_kw")


def _validate_payload(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    for k in _REQUIRED:
        if k not in payload or payload[k] in (None, ""):
            return f"{k} is required", None
    try:
        freq = float(payload["frequency_mhz"])
        erp = float(payload["erp_kw"])
    except (TypeError, ValueError):
        return "frequency_mhz / erp_kw must be numeric", None
    if freq <= 0 or freq > 100_000:
        return "frequency_mhz out of range", None
    if erp <= 0 or erp > 5000:
        return "erp_kw out of range (0..5000)", None

    return None, {
        "tx_qth_content":   str(payload["tx_qth_content"]),
        "tx_call":          str(payload.get("tx_call") or "TX"),
        "frequency_mhz":    freq,
        "erp_kw":           erp,
        "polarization":     str(payload.get("polarization") or "V"),
        "climate_code":     int(payload.get("climate_code") or 5),
        "max_distance_km":  float(payload.get("max_distance_km") or 80),
        "radial_step_deg":  float(payload.get("radial_step_deg") or 10),
        "target_field_dbu": float(payload.get("target_field_dbu") or 60),
        "rx_height_m":      float(payload.get("rx_height_m") or 10),
        "timeout_seconds":  int(payload.get("timeout_seconds") or 180),
    }


def _has_sdf_tiles(sdf_dir: Path) -> bool:
    if not sdf_dir.is_dir():
        return False
    for p in sdf_dir.iterdir():
        if p.is_file() and p.suffix in (".sdf", ".sdz"):
            return True
    return False


# ---------- route --------------------------------------------------

@bp.post("/api/v1/splat/run-inline")
def run_inline():
    if not _CFG:
        return jsonify({"error": "inline-runner not configured"}), 500
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

    workdir   = _CFG["workdir"]
    splat_bin = _CFG["splat_bin"]
    sdf_dir   = _CFG["sdf_dir_name"]
    run_dir   = (workdir / f"{_CFG['run_dir_prefix']}{uuid.uuid4().hex[:12]}").resolve()
    try:
        run_dir.relative_to(workdir)
    except ValueError:
        return jsonify({"error": "run dir escaped WORKDIR"}), 500
    run_dir.mkdir(parents=True, exist_ok=True)

    started        = time.monotonic()
    stdout_chunks: List[str] = []
    radials_out:   List[Dict[str, Any]] = []
    terrain_used   = _has_sdf_tiles(workdir / sdf_dir)

    try:
        tx_qth = run_dir / "TX.qth"
        tx_lrp = run_dir / "TX.lrp"
        try:
            tx_qth.write_text(_render_qth(params["tx_qth_content"]))
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        tx_lrp.write_text(_render_lrp(
            frequency_mhz=params["frequency_mhz"],
            erp_kw=params["erp_kw"],
            polarization=params["polarization"],
            climate_code=params["climate_code"],
        ))

        # Tx coords come out of the QTH file as W-positive (SPLAT
        # convention).  Convert to E-positive for the bearing math,
        # then back to W-positive when writing the rx QTH.
        tx_lat, tx_lon_w = _qth_latlon_w_pos(tx_qth.read_text())
        tx_lon_e = -tx_lon_w

        bearings = list(range(0, 360, max(1, int(params["radial_step_deg"]))))
        max_km   = float(params["max_distance_km"])
        target   = float(params["target_field_dbu"])
        rx_ht_ft = float(params["rx_height_m"]) * 3.28084
        per_radial_timeout = max(15, int(params["timeout_seconds"]) // max(1, len(bearings)))

        for az in bearings:
            rx_lat, rx_lon_e = _destination(tx_lat, tx_lon_e, az, max_km)
            rx_qth = run_dir / f"RX{az:03d}.qth"
            rx_qth.write_text(
                f"RX{az:03d}\n{rx_lat:.6f}\n{-rx_lon_e:.6f}\n{rx_ht_ft:.1f} feet\n"
            )

            cmd = [
                splat_bin,
                "-t", tx_qth.name,
                "-r", rx_qth.name,
                "-metric",
                "-L", f"{rx_ht_ft:.1f}",
                "-d", sdf_dir,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(run_dir),
                    capture_output=True,
                    text=True,
                    timeout=per_radial_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                radials_out.append({
                    "az": az,
                    "error": "splat timeout",
                    "terrain_distance_km": None,
                    "fcc_distance_km": None,
                    "samples": [],
                    "rx_lat": round(rx_lat, 6),
                    "rx_lon": round(rx_lon_e, 6),
                })
                continue
            except FileNotFoundError:
                # splat binary missing in the image.  Bail the whole
                # sweep — every radial would hit the same wall.
                return jsonify({
                    "available": False,
                    "source":    "splat-sidecar",
                    "error":     f"splat binary not found at {splat_bin}",
                    "hint":      "image was built without ./build all; rebuild with the current Dockerfile",
                }), 500

            if len(stdout_chunks) < 6:
                stdout_chunks.append(
                    f"--- az {az} rc={proc.returncode} ---\n"
                    f"{proc.stdout[:1500]}\n{proc.stderr[:500]}\n"
                )

            loss_file = _find_loss_file(run_dir, az)
            samples   = _parse_loss_table(loss_file) if loss_file else []
            for s in samples:
                if "field_dbu" not in s:
                    s["field_dbu"] = round(_path_loss_to_field_dbu(
                        s["path_loss_dB"],
                        frequency_mhz=params["frequency_mhz"],
                        erp_kw=params["erp_kw"],
                    ), 2)
            terrain_km = _interp_threshold_km(samples, target)

            radials_out.append({
                "az": az,
                "terrain_distance_km": (round(terrain_km, 3) if terrain_km is not None else None),
                "fcc_distance_km":     None,
                "samples":             samples,
                "rx_lat":              round(rx_lat, 6),
                "rx_lon":              round(rx_lon_e, 6),
                "returncode":          proc.returncode,
            })

        elapsed = time.monotonic() - started
        return jsonify({
            "available":        True,
            "source":           "splat-sidecar",
            "method":           "SPLAT (Longley-Rice ITWOM 3.0) per-radial p2p sweep",
            "engine":           "splat",
            "tier":             "inline",
            "dem_source":       ("sidecar-provisioned (sdf/)"
                                 if terrain_used else "flat-earth (no SDF tiles)"),
            "terrain_used":     terrain_used,
            "runtime_seconds":  round(elapsed, 3),
            "radial_count":     len(radials_out),
            "target_field_dbu": target,
            "max_distance_km":  max_km,
            "radials":          radials_out,
            "stdout":           "".join(stdout_chunks)[:8000],
        })
    finally:
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
