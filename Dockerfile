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

WORKDIR /app
COPY --from=builder /install /usr/local
COPY server/ ./server/

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
