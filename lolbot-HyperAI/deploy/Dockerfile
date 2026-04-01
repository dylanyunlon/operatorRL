###############################################################################
# OperatorRL lolbot-HyperAI · Dockerfile
# 自部署 自环境反馈 自演化
#
# Multi-stage build:
#   Stage 1 (builder) — install Python deps, run linting
#   Stage 2 (runtime) — minimal image with only production code
#
# Usage:
#   docker build -t lolbot-hyperai:latest .
#   docker run --rm -it lolbot-hyperai:latest
#   docker-compose up   (see docker-compose.yml)
#
# Design Decisions:
#   - Python 3.11-slim for balance of size vs ecosystem support
#   - Non-root user (lolbot:1000) for security
#   - /data mount for evolution state persistence across restarts
#   - Edge-TTS pre-installed for zero-config voice output
###############################################################################

# ============================================================
# Stage 1: Builder — install dependencies & validate
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for compilation (cleaned up in runtime stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY deploy/requirements.txt requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy source for validation
COPY . /build/lolbot-HyperAI

# Quick syntax check (non-blocking — build continues on failure)
RUN python -m py_compile /build/lolbot-HyperAI/runtime/process_manager.py || true
RUN python -m py_compile /build/lolbot-HyperAI/integration/event_dispatcher.py || true


# ============================================================
# Stage 2: Runtime — minimal production image
# ============================================================
FROM python:3.11-slim AS runtime

LABEL maintainer="OperatorRL Team"
LABEL description="lolbot-HyperAI: Self-deploying LoL AI Assistant"
LABEL version="1.0.0"

# Install runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    # For TTS audio playback
    libsndfile1 \
    # For /proc monitoring
    procps \
    # Timezone data
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -g 1000 lolbot && \
    useradd -u 1000 -g lolbot -m -s /bin/bash lolbot

# Application directory
WORKDIR /app/lolbot-HyperAI

# Copy application code
COPY --chown=lolbot:lolbot . /app/lolbot-HyperAI/

# Create data directories for persistence
RUN mkdir -p /data/checkpoints /data/evolution /data/logs /data/sessions && \
    chown -R lolbot:lolbot /data

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/lolbot-HyperAI \
    LOLBOT_DATA_DIR=/data \
    LOLBOT_LOG_DIR=/data/logs \
    LOLBOT_CHECKPOINT_DIR=/data/checkpoints \
    LOLBOT_EVOLUTION_DIR=/data/evolution \
    # Default: 10ms tick (100Hz)
    LOLBOT_BASE_TICK_MS=10 \
    # TTS backend: system, edge, openai, disabled
    LOLBOT_TTS_BACKEND=edge \
    # Fiddler MCP server URL
    LOLBOT_FIDDLER_URL=http://host.docker.internal:8868/mcp \
    # LCU fallback
    LOLBOT_LCU_HOST=host.docker.internal

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, urllib.request; \
        r = urllib.request.urlopen('http://localhost:8080/health'); \
        d = json.loads(r.read()); \
        exit(0 if d.get('overall') != 'unhealthy' else 1)" \
    || exit 1

# Expose metrics/dashboard port
EXPOSE 8080

# Switch to non-root user
USER lolbot

# Volumes for persistent data
VOLUME ["/data"]

# Entry point
ENTRYPOINT ["/app/lolbot-HyperAI/deploy/entrypoint.sh"]
CMD ["--mode", "full"]
