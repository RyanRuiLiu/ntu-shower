# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| `scripts/deploy.sh` | **不上服务器常驻**：随用随 `scp` 到 `/tmp` 再执行 | 见 `docs/RUNBOOK.md`「部署」 |
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-available/ntu-shower.conf`（软链到 sites-enabled） | `nginx -t && systemctl reload nginx` |
| `systemd/ntu-shower.service` | `/etc/systemd/system/ntu-shower.service` | `systemctl daemon-reload && systemctl restart ntu-shower` |
| `backup/ntu-shower-backup.sh` | `/opt/ntu-shower-backup/ntu-shower-backup.sh` | 由单元调用 |
| `backup/ntu-shower-backup.{service,timer}` | `/etc/systemd/system/` | `systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer` |
| `publish_apk.ps1` | **不上服务器**：本地工具 | `.\deploy\publish_apk.ps1 -Identity <admin>` |

代码替换、回滚与部署后验证的步骤见 `docs/RUNBOOK.md`；这里只放配置资产本身与同步命令。

## 同步命令

```bash
scp deploy/nginx/ntu-shower.conf root@iseekdream.com:/etc/nginx/sites-available/ntu-shower.conf
ssh root@iseekdream.com "nginx -t && systemctl reload nginx"

scp deploy/systemd/ntu-shower.service root@iseekdream.com:/etc/systemd/system/ntu-shower.service
ssh root@iseekdream.com "systemctl daemon-reload && systemctl restart ntu-shower"

scp deploy/backup/ntu-shower-backup.sh root@iseekdream.com:/opt/ntu-shower-backup/ntu-shower-backup.sh
scp deploy/backup/ntu-shower-backup.service deploy/backup/ntu-shower-backup.timer root@iseekdream.com:/etc/systemd/system/
ssh root@iseekdream.com "chmod 700 /opt/ntu-shower-backup/ntu-shower-backup.sh && systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer"
```

## 要点（改配置前先读）

- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头
  （`Upgrade` + `Connection "upgrade"` + `proxy_http_version 1.1`）：
  前者服务用户页 `/api/v1/stream`，后者服务设备网关 `/gateway/device`。
- `proxy_read_timeout 3600s`：设备网关与实时推送都是长连接，默认 60s 会被 nginx 掐断。
- `.env`（数据库路径等生产配置）只在服务器上，不入仓库。
