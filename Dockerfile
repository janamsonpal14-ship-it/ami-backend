# Multi-stage lightweight Python Dockerfile for AMI Backend
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

# Copy installed Python packages from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Copy application files
COPY app/ ./app/
COPY data/manifests/ ./data/manifests/
COPY data/launchd/ ./data/launchd/
COPY pytest.ini ./

# Expose default API port
EXPOSE 8000

# Launch uvicorn dynamically binding to $PORT (compatible with Render, Railway, Fly.io, Koyeb)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

