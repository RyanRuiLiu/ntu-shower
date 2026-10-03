# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器
（本次入库的原因：nginx 的 WebSocket 升级头曾游离在仓库外，漏配导致用户页实时推送断连）。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-available/ntu-shower.conf`（软链到 sites-enabled） | `nginx -t && systemctl reload nginx` |
| `systemd/ntu-shower.service` | `/etc/systemd/system/ntu-shower.service` | `systemctl daemon-reload && systemctl restart ntu-shower` |
| `backup/ntu-shower-backup.sh` | `/opt/ntu-shower-backup/ntu-shower-backup.sh` | 由单元调用 |
| `backup/ntu-shower-backup.{service,timer}` | `/etc/systemd/system/` | `systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer` |

**发布流程、备份、表结构变更的顺序见 `docs/RUNBOOK.md`**——这里只放配置资产本身。

## 同步命令

```bash
scp deploy/nginx/ntu-shower.conf deploy@<deploy-host>:/etc/nginx/sites-available/ntu-shower.conf
ssh deploy@<deploy-host> "nginx -t && systemctl reload nginx"

scp deploy/systemd/ntu-shower.service deploy@<deploy-host>:/etc/systemd/system/ntu-shower.service
ssh deploy@<deploy-host> "systemctl daemon-reload && systemctl restart ntu-shower"

scp deploy/backup/ntu-shower-backup.sh deploy@<deploy-host>:/opt/ntu-shower-backup/ntu-shower-backup.sh
scp deploy/backup/ntu-shower-backup.service deploy/backup/ntu-shower-backup.timer deploy@<deploy-host>:/etc/systemd/system/
ssh deploy@<deploy-host> "chmod +x /opt/ntu-shower-backup/ntu-shower-backup.sh && systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer"
```

## 要点（改配置前先读）

- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头
  （`Upgrade` + `Connection "upgrade"` + `proxy_http_version 1.1`）：
  前者服务用户页 `/api/v1/stream`，后者服务设备网关 `/gateway/device`。
- `proxy_read_timeout 3600s`：设备网关与实时推送都是长连接，默认 60s 会被 nginx 掐断。
- `.env`（数据库路径等生产配置）只在服务器上，不入仓库。
