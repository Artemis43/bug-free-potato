# ─────────────────────────────────────────────────────────────────────────────
#  razorpay_webhook / Dockerfile
#  Multi-stage build for a minimal production image (~120 MB).
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools (needed for psycopg2-binary compilation on some arches)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user for security
RUN useradd -m -u 1001 appuser

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Switch to non-root
USER appuser

# ── Runtime environment ───────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

EXPOSE 8000

# Uvicorn with:
#   --workers 2      → 2 worker processes (adjust to CPU count)
#   --proxy-headers  → trust X-Forwarded-* from reverse proxy
#   --no-access-log  → handled by structured logging in app.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx; r=httpx.get('http://localhost:8000/health',timeout=4); exit(0 if r.status_code==200 else 1)"

CMD ["uvicorn", "app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--no-access-log"]
