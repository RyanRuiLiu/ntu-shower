# syntax=docker/dockerfile:1
# 依据：项目运行时 Python 3.13（systemd venv 3.13.18）
FROM python:3.13-slim AS builder
WORKDIR /app
COPY server/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim
# 依据：Docker 官方 Python 指南（无缓冲输出、不写 .pyc）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 工作目录必须就是 server/ 包的父目录：`uvicorn app.main:app` 按相对模块路径解析，
# systemd 形态靠 WorkingDirectory=/opt/ntu-shower/server 成立，容器形态由本行承担。
WORKDIR /app/server
# 语音素材上传与转码依赖 ffmpeg/ffprobe（libmp3lame；见 CONTRIBUTING「环境」），
# slim 基镜像不带——systemd 形态装在宿主机，容器形态必须进镜像，否则
# /voice/status 的 ffmpeg_available 恒为 false、素材上传全被拒。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY server/ ./

# 依据：CIS Docker Benchmark 5.12（容器内非 root）
# 数据目录 /var/lib/ntu-shower 属主 UID/GID 997（项目事实，ntu-shower）
RUN chown -R 997:997 /app
USER 997:997

EXPOSE 8110

# 依据：Dockerfile reference HEALTHCHECK 默认值
# 健康端点 /healthz（项目事实，app/main.py:153）
HEALTHCHECK --interval=30s --timeout=30s --start-period=0s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

# 依据：项目 systemd ExecStart（uvicorn app.main:app --port 8110）
# 12-Factor VII（Port binding）：容器内监听 0.0.0.0
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
