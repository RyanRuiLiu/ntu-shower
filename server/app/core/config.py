"""配置：全部来自环境变量（.env），不在代码里硬编码任何环境值。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: str = "data/ntu-shower.db"
    #: 显式的数据库连接串（环境变量 DATABASE_URL）。留空时由 db_path 推导。
    #: 迁移工具与数据转换脚本用同一份来源，避免「迁移打的库和应用连的库」分叉。
    database_url: str = ""
    max_ota_bytes: int = 50 * 1024 * 1024  # OTA 上传/存储的单文件上限
    ota_dir: str = "ota_releases"  # APK 发布文件目录（相对 server 工作目录）
    voice_dir: str = "data/voice"  # 固定音频素材目录（assets/，随 data/ 保留）

    # ── 日志 ──
    # 应用日志级别（DEBUG/INFO/WARNING/ERROR）；uvicorn 的访问日志级别另由
    # systemd 单元决定（见 deploy/nginx 与 docs/runbook.md）
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

    # ── 管理台会话 ──
    # 会话签名密钥（Starlette SessionMiddleware / itsdangerous 的 secret_key）。
    # 必须由环境变量注入；留空时 create_app 拒绝启动——签名密钥是会话安全性的全部，
    # 进程内临时生成一份会随重启作废（多 worker 时各签各的，互相验不过），只会把
    # 「没配密钥」这个错误从启动时拖到第一次登录之后。这与 Flask（secret_key 未设
    # 即拒绝会话）、Django（SECRET_KEY 缺失即 ImproperlyConfigured）同一语义。
    session_secret: str = ""
    # 会话空闲超时（秒）：即 SessionMiddleware 的 max_age。活跃使用中每过
    # RENEW_AFTER_S（见 auth.dependencies）重签一次 Cookie，所以它只惩罚"连续
    # 这么久没用"，不影响活跃用户——OWASP Session CS 的 idle timeout + renewal。
    session_idle_seconds: int = 30 * 24 * 60 * 60
    # 会话绝对超时（秒）：距首次登录超过它即失效，服务端按会话里的 first_iat
    # 强制（OWASP absolute timeout）。自用面板按风险取宽值，两个数值都可调。
    session_absolute_seconds: int = 90 * 24 * 60 * 60
    # 设备接入令牌校验默认强制开启。只在测试夹具里显式关闭——生产不留
    # 「不带凭据也能连」的旁路，这是安全默认值而不是长期双轨。
    require_device_token: bool = True
    # 设备自注册的 claim 令牌（AWS IoT fleet provisioning by claim 同型）：APK 构建期
    # 内置一枚全队共享的注册令牌，设备首次连接凭它换每台自己的接入凭据；信任位照旧
    # 由管理员在档案上点——注册只回答「这是我们的程序」，不等于信任。留空即注册
    # 通道关闭（fail closed）。泄露时轮换：换一枚重新构建 APK，库里凭据不受影响。
    device_enroll_token: str = ""

    @property
    def effective_database_url(self) -> str:
        """应用与迁移共用的连接串：显式值优先，否则由 db_path 推导。

        `as_posix()`：Windows 下的反斜杠路径放进 `sqlite:///` 会被当成 URL 的一部分，
        与 `Path` 解析出的文件不是同一个；统一成正斜杠，两端才指向同一份库。
        """
        return self.database_url or f"sqlite:///{Path(self.db_path).as_posix()}"


settings = Settings()
