# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install dependencies into a separate layer so Docker cache is reused
# when only source code changes (not requirements).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /medbot

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source (excluding files listed in .dockerignore)
COPY . .

# Expose the Flask keep-alive port
EXPOSE 4343

# Run as a non-root user for security
RUN adduser --disabled-password --gecos "" botuser
USER botuser

# Use exec form so signals (SIGTERM) reach the Python process directly
CMD ["python", "main.py"]