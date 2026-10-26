FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir -e /app/backend \
  && useradd --create-home --uid 10001 app \
  && mkdir -p /data/storage && chown -R app:app /data /app
COPY --chown=app:app frontend/ /app/frontend/
COPY --chown=app:app scripts/ /app/scripts/
COPY --chown=app:app docs/ /app/docs/
COPY --chown=app:app fixtures/ /app/fixtures/
ENV PIANO_SCRIBE_STORAGE=/data/storage
VOLUME /data
EXPOSE 8000
USER app
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "piano_scribe.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
