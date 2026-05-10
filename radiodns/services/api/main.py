"""RadioDNS control plane API."""

import hashlib
import os
from datetime import datetime, timezone

import boto3
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
