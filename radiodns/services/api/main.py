"""RadioDNS control plane API."""

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import boto3
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="RadioDNS Control Plane", version="1.0.0")

REGION = os.environ["SPACES_REGION"]
ENDPOINT_URL = f"https://{REGION}.digitaloceanspaces.com"

BUCKETS = {
    "spi": os.environ["SPI_BUCKET"],
    "logos": os.environ["LOGOS_BUCKET"],
    "epg": os.environ["EPG_BUCKET"],
    "vis": os.environ["VIS_BUCKET"],
    "evidence": os.environ["EVIDENCE_BUCKET"],
}

CDN_HOSTS = {
    "spi": os.environ["SPI_CDN_HOST"],
    "logos": os.environ["LOGOS_CDN_HOST"],
    "epg": os.environ["EPG_CDN_HOST"],
    "vis": os.environ["VIS_CDN_HOST"],
    "evidence": os.environ["EVIDENCE_CDN_HOST"],
}


def s3_client():
    return boto3.client(
        "s3",
        region_name=REGION,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=os.environ["DO_SPACES_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET_KEY"],
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# SPI endpoints
# ---------------------------------------------------------------------------

class SPIPublishRequest(BaseModel):
    station_id: str
    version: str  # use "live" for the canonical short-TTL SI.xml


@app.post("/stations/{station_id}/spi")
async def publish_spi(station_id: str, version: str, file: UploadFile):
    content = await file.read()
    if version == "live":
        key = f"stations/{station_id}/spi/SI.xml"
        cache_control = "public, max-age=60, s-maxage=600"
    else:
        key = f"stations/{station_id}/spi/{version}/SI.xml"
        cache_control = "public, max-age=3600"

    s3_client().put_object(
        Bucket=BUCKETS["spi"],
        Key=key,
        Body=content,
        ContentType="application/xml",
        CacheControl=cache_control,
        ACL="public-read",
    )
    return {"url": f"https://{CDN_HOSTS['spi']}/{key}"}


# ---------------------------------------------------------------------------
# Logo endpoints
# ---------------------------------------------------------------------------

@app.post("/stations/{station_id}/logos")
async def publish_logo(station_id: str, file: UploadFile):
    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()
    ext = (file.filename or "logo.png").rsplit(".", 1)[-1].lower()
    key = f"stations/{station_id}/logos/{sha256}.{ext}"

    content_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}
    content_type = content_types.get(ext, "application/octet-stream")

    s3_client().put_object(
        Bucket=BUCKETS["logos"],
        Key=key,
        Body=content,
        ContentType=content_type,
        CacheControl="public, max-age=604800, immutable",
        ACL="public-read",
    )
    return {"url": f"https://{CDN_HOSTS['logos']}/{key}", "hash": sha256}


# ---------------------------------------------------------------------------
# Evidence endpoints
# ---------------------------------------------------------------------------

@app.post("/evidence/{capture_id}")
async def publish_evidence(capture_id: str, file: UploadFile):
    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()
    ext = (file.filename or "artifact.json").rsplit(".", 1)[-1].lower()
    key = f"evidence/{capture_id}/{sha256}.{ext}"

    s3_client().put_object(
        Bucket=BUCKETS["evidence"],
        Key=key,
        Body=content,
        ContentType="application/json",
        CacheControl="public, max-age=86400, immutable",
        ACL="public-read",
    )
    return {"url": f"https://{CDN_HOSTS['evidence']}/{key}", "hash": sha256}


# ---------------------------------------------------------------------------
# VIS endpoints
# ---------------------------------------------------------------------------

@app.post("/stations/{station_id}/vis")
async def publish_vis(station_id: str, file: UploadFile):
    content = await file.read()
    ts = int(datetime.now(timezone.utc).timestamp())
    ext = (file.filename or "slide.jpg").rsplit(".", 1)[-1].lower()
    key = f"stations/{station_id}/vis/{ts}.{ext}"

    s3_client().put_object(
        Bucket=BUCKETS["vis"],
        Key=key,
        Body=content,
        ContentType="image/jpeg",
        CacheControl="public, max-age=3600",
        ACL="public-read",
    )
    return {"url": f"https://{CDN_HOSTS['vis']}/{key}"}


# ---------------------------------------------------------------------------
# Async publish job queue
#
# POST /internal/jobs  — enqueue a publish job for the worker to process.
#
# The worker (radiodns/services/publisher/worker.py) polls
# radiodns_publish_jobs and executes each job by writing to Spaces.
# This endpoint is internal — callers must be on the App Platform
# private network (not exposed via public CDN/DNS).
#
# Request body:
#   { asset_type, bucket, key, content_type, body_b64 }
#   body_b64: base64-encoded asset bytes
# Response: { job_id }
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")

ENQUEUE_SQL = """
INSERT INTO radiodns_publish_jobs
    (asset_type, bucket, key, content_type, body_b64)
VALUES (%s, %s, %s, %s, %s)
RETURNING id;
"""

VALID_ASSET_TYPES = {"logo", "spi_live", "spi_snapshot", "epg", "vis", "evidence"}


class PublishJobRequest(BaseModel):
    asset_type:   str
    bucket:       str
    key:          str
    content_type: str
    body_b64:     str   # base64-encoded bytes


@app.post("/internal/jobs")
def enqueue_job(req: PublishJobRequest):
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured — job queue unavailable")
    if req.asset_type not in VALID_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown asset_type: {req.asset_type}. Valid: {sorted(VALID_ASSET_TYPES)}")
    try:
        base64.b64decode(req.body_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="body_b64 is not valid base64")

    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        with conn:
            with conn.cursor() as cur:
                cur.execute(ENQUEUE_SQL, (req.asset_type, req.bucket, req.key, req.content_type, req.body_b64))
                row = cur.fetchone()
        conn.close()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail=f"database error: {exc}")

    return {"job_id": str(row["id"])}


@app.get("/internal/jobs/{job_id}")
def get_job(job_id: str):
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asset_type, bucket, key, status, error, created_at, updated_at FROM radiodns_publish_jobs WHERE id=%s",
                    (job_id,),
                )
                row = cur.fetchone()
        conn.close()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail=f"database error: {exc}")

    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return dict(row)
