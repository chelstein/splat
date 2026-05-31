"""RadioDNS publisher worker.

Polls the radiodns_publish_jobs Postgres table for QUEUED rows and
writes assets to Spaces with the correct Cache-Control headers and
content-addressed object paths.

JOB TABLE (auto-created on startup):

  CREATE TABLE radiodns_publish_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_type    TEXT NOT NULL,     -- spi_live | spi_snapshot | logo | vis | evidence | epg
    bucket        TEXT NOT NULL,
    key           TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    body_b64      TEXT NOT NULL,     -- base64-encoded body bytes
    status        TEXT NOT NULL DEFAULT 'queued',   -- queued | running | complete | failed
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );

Jobs are claimed with FOR UPDATE SKIP LOCKED so multiple worker
replicas don't double-process the same row.

ENV:
  DATABASE_URL            Postgres DSN (required)
  SPACES_REGION           e.g. nyc3
  DO_SPACES_ACCESS_KEY    Spaces credentials
  DO_SPACES_SECRET_KEY
  WORKER_POLL_SECONDS     poll interval (default 10)
  WORKER_MAX_PER_TICK     max jobs per poll tick (default 5)
"""

import base64
import logging
import os
import time
import uuid

import boto3
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL  = os.environ.get("DATABASE_URL")
REGION        = os.environ["SPACES_REGION"]
ENDPOINT_URL  = f"https://{REGION}.digitaloceanspaces.com"
POLL_SECONDS  = int(os.environ.get("WORKER_POLL_SECONDS", "10"))
MAX_PER_TICK  = int(os.environ.get("WORKER_MAX_PER_TICK", "5"))

CACHE_CONTROLS = {
    "logo":         "public, max-age=604800, immutable",
    "spi_live":     "public, max-age=60, s-maxage=600",
    "spi_snapshot": "public, max-age=3600",
    "epg":          "public, max-age=3600",
    "vis":          "public, max-age=3600",
    "evidence":     "public, max-age=86400, immutable",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS radiodns_publish_jobs (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_type    TEXT         NOT NULL,
    bucket        TEXT         NOT NULL,
    key           TEXT         NOT NULL,
    content_type  TEXT         NOT NULL,
    body_b64      TEXT         NOT NULL,
    status        TEXT         NOT NULL DEFAULT 'queued',
    error         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS radiodns_publish_jobs_status_idx
    ON radiodns_publish_jobs (status, created_at);
"""


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def ensure_schema():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
    log.info("radiodns_publish_jobs schema ready")


def s3_client():
    return boto3.client(
        "s3",
        region_name=REGION,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=os.environ["DO_SPACES_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET_KEY"],
    )


def claim_jobs(conn) -> list:
    """Claim up to MAX_PER_TICK queued jobs atomically using SKIP LOCKED."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE radiodns_publish_jobs
               SET status     = 'running',
                   updated_at = NOW()
             WHERE id IN (
               SELECT id FROM radiodns_publish_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT %s
                  FOR UPDATE SKIP LOCKED
             )
             RETURNING id, asset_type, bucket, key, content_type, body_b64
            """,
            (MAX_PER_TICK,),
        )
        rows = cur.fetchall()
    conn.commit()
    return rows


def mark_complete(conn, job_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE radiodns_publish_jobs SET status='complete', updated_at=NOW() WHERE id=%s",
            (str(job_id),),
        )
    conn.commit()


def mark_failed(conn, job_id: str, error: str):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE radiodns_publish_jobs SET status='failed', error=%s, updated_at=NOW() WHERE id=%s",
            (error[:2000], str(job_id)),
        )
    conn.commit()


def process_job(job: dict):
    asset_type   = job["asset_type"]
    bucket       = job["bucket"]
    key          = job["key"]
    content_type = job["content_type"]
    body         = base64.b64decode(job["body_b64"])
    cache_control = CACHE_CONTROLS.get(asset_type, "public, max-age=3600")

    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl=cache_control,
        ACL="public-read",
    )
    log.info("published s3://%s/%s (%s, %d bytes)", bucket, key, asset_type, len(body))


def tick(conn):
    jobs = claim_jobs(conn)
    if not jobs:
        return
    log.info("claimed %d job(s)", len(jobs))
    for job in jobs:
        try:
            process_job(job)
            mark_complete(conn, job["id"])
        except Exception as exc:
            log.error("job %s failed: %s", job["id"], exc)
            mark_failed(conn, job["id"], str(exc))


def run():
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set — worker running in direct-publish mode only (no job queue)")
        # Without a DB the worker has no queue to poll; just stay alive
        # so App Platform doesn't restart it in a crash loop.
        while True:
            time.sleep(POLL_SECONDS)

    log.info("radiodns-publisher starting (poll=%ss max_per_tick=%s)", POLL_SECONDS, MAX_PER_TICK)
    ensure_schema()

    while True:
        try:
            with get_db() as conn:
                tick(conn)
        except Exception as exc:
            log.error("tick error: %s", exc)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
