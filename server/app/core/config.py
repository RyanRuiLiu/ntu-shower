"""配置：全部来自环境变量（.env），不在代码里硬编码任何环境值。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: str = "data/ntu-shower.db"
    max_ota_bytes: int = 50 * 1024 * 1024  # OTA 上传/存储的单文件上限
    ota_dir: str = "ota_releases"  # APK 发布文件目录（相对 server 工作目录）

    # ── 对外地址 ──
    public_base_url: str = ""  # 对外基址（RFC 9457 问题 type 等绝对引用用）；留空则输出相对引用

    # ── 鉴权 ──
    admin_token: str = "change-me"  # 管理后台静态令牌（运维通道），占位值会在启动自检中被拒绝
    config_ack_timeout_s: float = 15.0  # 等待设备 config.ack 的时限


settings = Settings()
