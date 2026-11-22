# syntax=docker/dockerfile:1
# 基镜像按 digest 钉版（tag 供人读，digest 承诺内容不可变）；升版 = 换 digest，走正常评审
FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS builder

COPY server/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt


FROM python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

COPY --from=builder /install /usr/local

WORKDIR /app/server

# 网页端是零构建静态树：源码即产物，随 server/ 一起进运行镜像
COPY --chown=997:997 server/ ./

RUN mkdir -p /var/lib/ntu-shower && chown 997:997 /var/lib/ntu-shower

USER 997:997

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

# 单进程 uvicorn：进程内存态（网关连接表、core.device_clock 的偏差缓存）以单进程为
# 前提；横向扩容前必须先把这类状态外置，而不是加 --workers。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
