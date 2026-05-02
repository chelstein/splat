FROM debian:bookworm AS builder
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    zlib1g-dev \
    libbz2-dev \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN chmod +x build && ./build splat

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /src/splat /app/splat
COPY genoa_sidecar.py dashboard.html ./
RUN chmod +x /app/splat /app/genoa_sidecar.py && mkdir -p /app/work
EXPOSE 8080
ENV GENOA_HOST=0.0.0.0 \
    GENOA_PORT=8080 \
    SPLAT_BIN=/app/splat \
    SPLAT_WORKDIR=/app/work
CMD ["python", "/app/genoa_sidecar.py"]
