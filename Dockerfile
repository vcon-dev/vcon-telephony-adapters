# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Runtime deps only; build tooling for wheels that need compiling
# (cryptography, etc.) is removed after install to keep the image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY core ./core
COPY adapters ./adapters
COPY twilio_adapter ./twilio_adapter
COPY main.py ./

RUN pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /root/.cache

# Run as non-root.
RUN useradd --create-home --shell /usr/sbin/nologin adapter
USER adapter

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8080}/health" || exit 1

ENTRYPOINT ["python", "main.py"]
CMD ["twilio"]
