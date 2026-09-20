# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8000 \
    RAG_DATABASE_URL=sqlite:////data/db/multimodal_rag.db \
    RAG_IMAGE_DIR=/data/images \
    HF_HOME=/app/local/models/huggingface \
    HF_HUB_DISABLE_XET=1

WORKDIR /app

# Docling's image-processing dependencies; no compiler or development tools.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        fontconfig \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-noto-core \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*
    
COPY requirements.txt ./
# Install CPU wheels first to avoid unnecessary CUDA libraries on Linux.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r requirements.txt \
    && python -m pip check

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /data/db /data/images /app/local/models \
    && chown -R app:app /data /app/local

FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements-dev.txt \
    && python -m pip check
COPY app ./app
COPY tests ./tests
USER app
ENV RAG_DATABASE_URL=sqlite:////tmp/test-app.db
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
COPY app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('RAG_PORT', '8000') + '/health', timeout=3).close()"
CMD ["python", "-m", "app.main"]
