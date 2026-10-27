"""配置：全部来自环境变量（.env），不在代码里硬编码任何环境值。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: str = "data/ntu-shower.db"
    max_ota_bytes: int = 50 * 1024 * 1024  # OTA 上传/存储的单文件上限
    ota_dir: str = "ota_releases"  # APK 发布文件目录（相对 server 工作目录）
    voice_dir: str = "data/voice"  # 固定音频素材目录（assets/，随 data/ 保留）

    # ── 日志 ──
    # 应用日志级别（DEBUG/INFO/WARNING/ERROR）；uvicorn 的访问日志级别另由
    # systemd 单元决定（见 deploy/nginx 与 docs/RUNBOOK.md）
    log_level: str = "INFO"

    # ── 审计保留（NIST SP 800-92：按运维需要定保留期并执行）──
    # api_call 审计行的保留天数，过期行由每日清理任务删除
    audit_retention_days: int = 90

    # ── OTA 发布保留 ──
    # 磁盘上至多保留最近 N 个发布（新版登记时裁旧）。0.4.2 这类上一版要留作回滚点，
    # 因此 N 不能是 1；发布文件一个 ~1MB，无上限的积累没有意义。
    ota_release_keep: int = 5

    # ── 对外地址 ──
    public_base_url: str = ""  # 对外基址（RFC 9457 问题 type 等绝对引用用）；留空则输出相对引用

    # ── 命令等待上限（秒）──
    # 是环境值：测试需要注入短超时，硬编码会让依赖超时的用例白等一分钟
    assign_timeout_s: float = 60.0
    settle_timeout_s: float = 60.0
    photo_timeout_s: float = 15.0

    # ── ADB 桥接 ──
    # 回环监听端口基址（每台设备顺延取用）。是环境值：测试与并行实例需要
    # 不同的端口段，硬编码会让"本机已占用该端口"变成测试失败。
    bridge_base_port: int = 15655

    # ── 命令确认等待 ──
    config_ack_timeout_s: float = 15.0  # 等待设备 config.ack 的时限


settings = Settings()
