FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir -e /app/backend
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/
COPY docs/ /app/docs/
COPY fixtures/ /app/fixtures/
ENV PIANO_SCRIBE_STORAGE=/data/storage
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "piano_scribe.api:app", "--host", "0.0.0.0", "--port", "8000"]
