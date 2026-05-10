"""RadioDNS publisher worker.

Polls for pending publish jobs and writes assets to Spaces with the correct
Cache-Control headers and content-addressed object paths.
"""

import hashlib
import logging
import os
import time

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REGION = os.environ["SPACES_REGION"]
ENDPOINT_URL = f"https://{REGION}.digitaloceanspaces.com"

CACHE_CONTROLS = {
    "logo": "public, max-age=604800, immutable",
    "spi_live": "public, max-age=60, s-maxage=600",
    "spi_snapshot": "public, max-age=3600",
    "epg": "public, max-age=3600",
    "vis": "public, max-age=3600",
    "evidence": "public, max-age=86400, immutable",
}


def s3():
    return boto3.client(
        "s3",
        region_name=REGION,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=os.environ["DO_SPACES_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET_KEY"],
    )


def publish(bucket: str, key: str, body: bytes, content_type: str, asset_type: str):
    s3().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl=CACHE_CONTROLS[asset_type],
        ACL="public-read",
    )
    log.info("published s3://%s/%s (%s)", bucket, key, asset_type)


def run():
    log.info("radiodns-publisher starting")
    while True:
        # TODO: replace with queue-based job polling (e.g. SQS-compatible)
        time.sleep(10)


if __name__ == "__main__":
    run()
