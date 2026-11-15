# syntax=docker/dockerfile:1
# 基镜像按 digest 钉版（tag 供人读，digest 承诺内容不可变）；升版 = 换 digest，走正常评审
FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS builder

COPY server/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

# 前端构建阶段：产物（dist/）进运行镜像，源码与 node_modules 不进
FROM node:26-alpine AS web-builder

WORKDIR /build
COPY server/web/package.json server/web/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    corepack enable pnpm && pnpm install --frozen-lockfile
COPY server/web/ ./
RUN pnpm exec tsc -b && pnpm run build


FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

COPY --from=builder /install /usr/local

WORKDIR /app/server

COPY --chown=997:997 server/ ./
# 前端构建产物覆盖源码目录：服务端 StaticFiles/SPA fallback 挂载的就是 dist
COPY --from=web-builder --chown=997:997 /build/dist/ ./web/

RUN mkdir -p /var/lib/ntu-shower && chown 997:997 /var/lib/ntu-shower

USER 997:997

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
