# syntax=docker/dockerfile:1
FROM python:3.13.13-slim-bookworm AS builder

COPY server/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt


FROM python:3.13.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app/server

COPY --chown=997:997 server/ ./

RUN mkdir -p /var/lib/ntu-shower && chown 997:997 /var/lib/ntu-shower

USER 997:997

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
