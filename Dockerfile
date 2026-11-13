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

# ffmpeg/ffprobe 静态二进制（含 libmp3lame）：apt 全量编解码库是镜像最大层
# （压缩后 164MB），静态方案实测把该层压到 108.5MB。镜像按 digest 钉版
# 保证内容不可变；冒烟以与 normalize_to_mp3 相同的参数实转一段音频，二进制
# 不可用或缺编解码器时构建即失败。
COPY --from=mwader/static-ffmpeg@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 \
    /ffmpeg /ffprobe /usr/local/bin/
RUN ffmpeg -hide_banner -loglevel error -f lavfi -i sine=frequency=440:duration=1 \
        -vn -ac 1 -ar 44100 -b:a 128k /tmp/ffmpeg-smoke.mp3 \
    && test -s /tmp/ffmpeg-smoke.mp3 \
    && ffprobe -hide_banner -loglevel error -show_entries format=duration -of csv=p=0 /tmp/ffmpeg-smoke.mp3 \
    && rm -f /tmp/ffmpeg-smoke.mp3

COPY --from=builder /install /usr/local

WORKDIR /app/server

COPY --chown=997:997 server/ ./

RUN mkdir -p /var/lib/ntu-shower && chown 997:997 /var/lib/ntu-shower

USER 997:997

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
