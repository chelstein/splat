# Genoa SPLAT sidecar — DigitalOcean App Platform image.
#
# This repo IS the SPLAT source (no nested /splat directory).  COPY .
# brings the entire build tree, the SPLAT build scripts (configure /
# build / install / clean) get +x, and we run the genoa_sidecar Flask
# app under gunicorn so DO can bind to ${PORT:-8080} cleanly.
#
# Build args:
#   GIT_COMMIT_SHA — stamped into /version (audit trail). Optional;
#                    defaults to "unknown".
#   BUILD_TIME     — stamped into /version. Optional.
#   SPLAT_MAXPAGES — page count baked into splat.h before compile.
#                    Default 9 = 3x3 deg analysis region (~52 MB RAM
#                    budget).  See ./configure for the full table.

FROM python:3.12-slim

ARG GIT_COMMIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG SPLAT_MAXPAGES=9

WORKDIR /app

# build-essential / g++ / make for ./build all; libbz2-dev is required
# for splat (-lbz2) and srtm2sdf (-lbz2); zlib1g-dev is required for
# fontdata (-lz).  Without these the prior image silently shipped no
# splat binary at all and /api/v1/splat/run leaked an HTTP 500 from
# subprocess FileNotFoundError.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    make \
    libbz2-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN chmod +x configure build install clean utils/build utils/install || true

# Bake std-parms.h non-interactively so ./build can pick it up without
# running the menu-driven configure script.  Skip the optional HD build
# (no hd-parms.h) since the sidecar runs in 3 arc-second mode.
RUN printf '/* Generated non-interactively in Dockerfile. */\n#define HD_MODE 0\n#define MAXPAGES %s\n' "$SPLAT_MAXPAGES" > std-parms.h \
    && rm -f hd-parms.h \
    && ./build all \
    && test -x /app/splat \
    && test -x /app/utils/srtm2sdf \
    && test -x /app/utils/usgs2sdf

# test_p2p: minimal CLI thunk into point_to_point_ITM, the same
# function splat invokes for site-report site-to-site path loss.
# Used by the splat-vs-JS bake-off harness in chelstein/itmlogic.
# Compile flags mirror ./build's splat target; we only link the
# itwom3.0.cpp translation unit (point_to_point_ITM is defined there)
# so we don't pull in splat.cpp's main() and 200KB of other code.
RUN g++ -O2 -s -fomit-frame-pointer -ffast-math -pipe \
        itwom3.0.cpp test_p2p.cpp -lm -lbz2 -o test_p2p \
    && test -x /app/test_p2p

ENV GENOA_HOST=0.0.0.0 \
    GENOA_PORT=8080 \
    SPLAT_BIN=/app/splat \
    SPLAT_WORKDIR=/app/work \
    SPLAT_UTILS_DIR=/app/utils \
    SPLAT_TEST_P2P_BIN=/app/test_p2p \
    GIT_COMMIT_SHA=${GIT_COMMIT_SHA} \
    BUILD_TIME=${BUILD_TIME}

EXPOSE 8080

# DigitalOcean App Platform sets $PORT; default to 8080 locally.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 180 genoa_sidecar:app"]
