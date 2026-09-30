"""配置：全部来自环境变量（.env），不在代码里硬编码任何环境值。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: str = "data/ntu-shower.db"
    max_ota_bytes: int = 50 * 1024 * 1024  # OTA 上传/存储的单文件上限
    ota_dir: str = "ota_releases"  # APK 发布文件目录（相对 server 工作目录）

    # ── 鉴权 ──
    admin_token: str = "change-me"  # 管理后台静态令牌（运维通道）
    secret_key: str = "change-me-secret"  # JWT 签名密钥
    access_token_expire_minutes: int = 24 * 60
    config_ack_timeout_s: float = 15.0  # 等待设备 config.ack 的时限


settings = Settings()
