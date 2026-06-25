FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=on \
    FORENSICS_MODEL_ROOT=/app/model_assets

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    gcc \
    git \
    libcairo2 \
    libffi-dev \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libgomp1 \
    libmagic-dev \
    libmagic1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpq-dev \
    libsndfile1 \
    netcat-openbsd \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app
RUN mkdir -p /app/media /app/private_media /app/staticroot /app/model_assets \
    && chown -R appuser:appuser /app \
    && chmod +x /app/deploy.sh

USER appuser
RUN python /app/scripts/prepare_model_assets.py
