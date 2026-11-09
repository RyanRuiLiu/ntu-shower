# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| 根目录 `compose.yaml` | `/opt/ntu-shower/compose.yaml` | `NTU_SHOWER_TAG=<tag> docker compose -f /opt/ntu-shower/compose.yaml up -d`（见 `docs/runbook.md`「部署」） |
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-enabled/ntu-shower.iseekdream.com.conf`（**独立文件，非软链**：sites-available 与 enabled 是两份拷贝，只写 enabled 才生效——2026-11-09 发布 0.6.6 时 413 排查确认） | `nginx -t && systemctl reload nginx` |
| `systemd/ntu-shower.service` | `/etc/systemd/system/ntu-shower.service` | **2026-11-06 起停用**（服务由 Compose 承载）；单元与 `/opt/ntu-shower/server` 的 venv 冻结保留，仅作应急回滚 |
| `scripts/deploy.sh` | **不上服务器常驻**（Docker 形态下不再使用，保留于仓库存档） | — |
| `backup/ntu-shower-backup.sh` | `/opt/ntu-shower/scripts/ntu-shower-backup.sh` | 由单元调用（宿主机执行，与容器无关） |
| `backup/ntu-shower-backup.{service,timer}` | `/etc/systemd/system/` | `systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer` |
| `publish_apk.ps1` | **不上服务器**：本地工具 | `.\deploy\publish_apk.ps1 -Identity <admin>` |

代码替换、回滚与部署后验证的步骤见 `docs/runbook.md`；这里只放配置资产本身与同步命令。

## 同步命令

```bash
scp compose.yaml root@iseekdream.com:/opt/ntu-shower/compose.yaml

scp deploy/nginx/ntu-shower.conf root@iseekdream.com:/etc/nginx/sites-enabled/ntu-shower.iseekdream.com.conf
ssh root@iseekdream.com "nginx -t && systemctl reload nginx"

scp deploy/backup/ntu-shower-backup.sh root@iseekdream.com:/opt/ntu-shower/scripts/ntu-shower-backup.sh
scp deploy/backup/ntu-shower-backup.service deploy/backup/ntu-shower-backup.timer root@iseekdream.com:/etc/systemd/system/
ssh root@iseekdream.com "chmod 700 /opt/ntu-shower/scripts/ntu-shower-backup.sh && systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer"
```

## 要点（改配置前先读）

- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头
  （`Upgrade` + `Connection "upgrade"` + `proxy_http_version 1.1`）：
  前者服务用户页 `/api/v1/stream`，后者服务设备网关 `/gateway/device`。
- `proxy_read_timeout 3600s`：设备网关与实时推送都是长连接，默认 60s 会被 nginx 掐断。
- TLS 策略（纯 AEAD，无 CBC）现在就在 `nginx/ntu-shower.conf` 的 server 块内声明：
  证书是单张 ECDSA 通配符，`ssl_ciphers` 只列三个 AEAD ECDSA 套件（TLS 1.3 的套件
  由 OpenSSL 默认提供，不经该指令）。不要再依赖全局 `/etc/nginx/nginx.conf` 承载
  TLS 策略——那会让仓库失去对安全关键配置的唯一事实源。
- `.env`（数据库路径等生产配置）只在服务器上（`/etc/ntu-shower/ntu-shower.env`），不入仓库。
