# syntax=docker/dockerfile:1.7
# =============================================================================
# Multi-Agent Local LLM Swarm — orchestrator worker image
# =============================================================================
# The image contains the orchestration loop, persona registry, tool whitelist
# and the task API. It contains no model weights: inference runs in the
# llm-runtime service and is reached over the Compose bridge network.
#
# Build context is the repository root:
#   docker build -f docker/worker/Dockerfile -t cloudguard/llm-swarm:local .
#
# The runtime user is unprivileged (uid/gid 10001) and Compose mounts the rootfs
# read-only, so a compromised agent cannot modify its own code.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="CloudGuard Multi-Agent Local LLM Swarm" \
      org.opencontainers.image.description="Multi-agent orchestrator over a local Ollama/vLLM inference server" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/kaveh8866/Multi-Agent-Local-LLM-Swarm"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SWARM_CONFIG_DIR=/app/configs \
    SWARM_STATE_DIR=/app/state

RUN groupadd --gid 10001 swarm \
 && useradd --uid 10001 --gid swarm --no-create-home --shell /usr/sbin/nologin swarm

WORKDIR /app

# Dependency layer first so source edits do not invalidate installed packages.
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs

# /app/state is a named volume in Compose; fixing ownership here lets the
# unprivileged user write run records without a root chown at runtime.
RUN mkdir -p /app/state \
 && chown -R 10001:10001 /app/state \
 && pip install --no-cache-dir . \
 && rm -rf /root/.cache /tmp/*

USER 10001:10001

ENTRYPOINT ["python"]
CMD ["-m", "swarm.worker"]
