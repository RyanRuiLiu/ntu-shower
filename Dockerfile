# syntax=docker/dockerfile:1

# 基础镜像钉到具体补丁版本：`3.13-slim` 会随上游漂移，
# 钉死补丁号让「换了一台机器构建出不同行为」这件事不发生。
# 依赖装到独立前缀再整体复制：比 venv 少一层 pip/setuptools，运行阶段不需要包管理器。
FROM python:3.13.13-slim-bookworm AS builder

COPY server/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt


FROM python:3.13.13-slim-bookworm AS runtime

# PYTHONUNBUFFERED：日志立即进 stdout，不被缓冲吞掉（12-Factor XI）
# PYTHONDONTWRITEBYTECODE：只读根文件系统下不产生 .pyc
# PYTHONFAULTHANDLER：崩溃时打印 Python 栈而不是只有信号名
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

# 语音素材的转码依赖 ffmpeg/ffprobe（libmp3lame）。slim 基镜像不带，
# 不进镜像的话 /voice/status 的 ffmpeg_available 恒为 false、素材上传全被拒。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# 工作目录必须是 server/ 包的父目录：uvicorn 按相对模块路径解析 app.main:app
WORKDIR /app/server

# 直接带属主复制，避免再跑一层 chown——chown 会复制整层文件，镜像凭空大一倍
COPY --chown=997:997 server/ ./

# 数据目录必须在镜像里就存在且属主正确：命名卷首次挂载时会继承镜像中的属主，
# 缺了这一步，卷会以 root 建出来，容器内 997 写不进去。
RUN mkdir -p /var/lib/ntu-shower && chown 997:997 /var/lib/ntu-shower

# 容器内非 root；UID 与宿主数据目录属主一致（项目事实：997）
USER 997:997

EXPOSE 8110

# 探针用镜像内的 Python 标准库，不额外装 curl
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

# 12-Factor VII：容器内监听 0.0.0.0，由编排层决定暴露范围
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
