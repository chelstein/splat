FROM python:3.12-slim
WORKDIR /app
COPY genoa_sidecar.py dashboard.html ./
COPY splat /app/splat
RUN chmod +x /app/genoa_sidecar.py /app/splat
EXPOSE 8080
ENV GENOA_HOST=0.0.0.0 GENOA_PORT=8080 SPLAT_BIN=/app/splat SPLAT_WORKDIR=/app/work
CMD ["python","/app/genoa_sidecar.py"]
