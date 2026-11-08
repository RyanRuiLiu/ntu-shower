from app.core.db import get_conn


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS device (
            device_id    TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            trusted      INTEGER NOT NULL DEFAULT 0,
            app_version  TEXT NOT NULL DEFAULT '',
            version_code INTEGER NOT NULL DEFAULT 0,
            os_version   TEXT NOT NULL DEFAULT '',
            capabilities TEXT NOT NULL DEFAULT '[]',
            last_seen_at TEXT NOT NULL DEFAULT '',
            last_ip      TEXT NOT NULL DEFAULT '',
            booted_at    TEXT NOT NULL DEFAULT '',
            -- 设备钟与服务端的偏差（秒）。用来把帧里的 open_time / in_time 平移到
            -- 服务端时间轴——那两个字段是"这次会话从什么时候开始"的唯一线索。
            -- 持久化是必须的：只留内存的话，服务重启后回到 0 等于这段时间不平移，而
            -- "我们刚接手、那边还被占着"恰恰最常发生在设备上线到首次 pong 之间。
            -- 见 core/device_clock。
            clock_skew_s REAL NOT NULL DEFAULT 0,
            -- 展示顺序：管理员手动调整（设备页 ↑/↓），全部设备清单按它排，数字段
            -- 之外的同序回落到名字的自然排序。新设备排在末尾。
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        -- 设备上下线。帧是**事件驱动**的（状态变化才上报），空闲时不上报，所以
        -- "没消息"既可能是"没人用"也可能是"设备离线"——两者在数据里长得一模一样、
        -- 事后无法区分。这张表把它分开，同时给出设备利用率的分母。
        --
        -- reason 记断开的原因：设备侧分类随 hello 带回（dns/tls/tcp/closed:<码>），
        -- 服务端自己的断开也记（pong-timeout）。空串 = 没有结构化原因（0.4.35 之前
        -- 的历史行、或服务端未分类的断开）。
        CREATE TABLE IF NOT EXISTS device_link_event (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            event       TEXT NOT NULL,
            reason      TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_device_link_event
            ON device_link_event(device_id, occurred_at);

        -- 设备日志上送：设备每次握手成功后把 device.log / crash.log 的尾部送到这里。
        -- 回答"设备离线期间发生了什么"——证据在回连那一刻自动到位，不依赖现场读文件。
        -- 有界：每个（设备，种类）只留最近 LOG_KEEP 份，插入时裁剪。
        CREATE TABLE IF NOT EXISTS device_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT NOT NULL,
            kind        TEXT NOT NULL,
            content     TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_device_log
            ON device_log(device_id, kind, id);
        """
    )
    # 存量库的补列：CREATE TABLE IF NOT EXISTS 对已存在的表不生效，一次性 ALTER 补上。
    # 重跑无害。
    columns = [row[1] for row in get_conn().execute("PRAGMA table_info(device)")]
    if "sort_order" not in columns:
        get_conn().execute("ALTER TABLE device ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    link_columns = [row[1] for row in get_conn().execute("PRAGMA table_info(device_link_event)")]
    if "reason" not in link_columns:
        get_conn().execute("ALTER TABLE device_link_event ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    get_conn().commit()
