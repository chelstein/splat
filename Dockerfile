# Genoa SPLAT sidecar — DigitalOcean App Platform image.
#
# This repo IS the SPLAT source (no nested /splat directory).  COPY .
# brings the entire build tree, the SPLAT build scripts (configure /
# build / install / clean) get +x, and we run the genoa_sidecar Flask
# app under gunicorn so DO can bind to ${PORT:-8080} cleanly.
#
# Build args:
#   GIT_COMMIT_SHA — stamped via gunicorn env (optional; for audit).

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN chmod +x configure build install clean || true

ENV GENOA_HOST=0.0.0.0 \
    GENOA_PORT=8080 \
    SPLAT_BIN=/app/splat \
    SPLAT_WORKDIR=/app/work

EXPOSE 8080

# DigitalOcean App Platform sets $PORT; default to 8080 locally.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 180 genoa_sidecar:app"]
