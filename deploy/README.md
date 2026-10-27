# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器
（本次入库的原因：nginx 的 WebSocket 升级头曾游离在仓库外，漏配导致用户页实时推送断连）。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| `scripts/deploy.sh` | **不上服务器常驻**：随用随 `scp` 到 `/tmp` 再执行 | 见下「部署」 |
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-available/ntu-shower.conf`（软链到 sites-enabled） | `nginx -t && systemctl reload nginx` |
| `systemd/ntu-shower.service` | `/etc/systemd/system/ntu-shower.service` | `systemctl daemon-reload && systemctl restart ntu-shower` |
| `backup/ntu-shower-backup.sh` | `/opt/ntu-shower-backup/ntu-shower-backup.sh` | 由单元调用 |
| `backup/ntu-shower-backup.{service,timer}` | `/etc/systemd/system/` | `systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer` |
| `publish_apk.ps1` | **不上服务器**：本地工具 | `.\deploy\publish_apk.ps1 -Identity <admin>` |

**发布流程的顺序见 `docs/RUNBOOK.md`**——这里只放配置资产本身。

## 部署

代码替换由 `scripts/deploy.sh` 完成（停服 → 备份 → **保留四项状态目录、其余全删**后再解包
→ 启动验证 → 失败自动回滚）。**四项保留项与"为什么不是列举要删的"写在脚本头部注释里**，
那是它的机制说明，此处不复述。

脚本**不常驻服务器**（随包发布的只有 `server/` 的内容），每次部署时送过去再执行：

```bash
scp deploy/scripts/deploy.sh <deploy-host>:/tmp/
ssh <deploy-host> 'sudo NT_TARBALL=/tmp/server_deploy.tar.gz bash /tmp/deploy.sh'
```

回滚：

```bash
ssh <deploy-host> 'ls -1t /opt/ntu-shower/backups/server-*.tar.gz | head'
ssh <deploy-host> 'sudo NT_ROLLBACK=/opt/ntu-shower/backups/server-<时间戳>.tar.gz bash /tmp/deploy.sh --rollback'
```

## 同步命令

```bash
scp deploy/nginx/ntu-shower.conf deploy@<deploy-host>:/etc/nginx/sites-available/ntu-shower.conf
ssh deploy@<deploy-host> "nginx -t && systemctl reload nginx"

scp deploy/systemd/ntu-shower.service deploy@<deploy-host>:/etc/systemd/system/ntu-shower.service
ssh deploy@<deploy-host> "systemctl daemon-reload && systemctl restart ntu-shower"

scp deploy/backup/ntu-shower-backup.sh deploy@<deploy-host>:/opt/ntu-shower-backup/ntu-shower-backup.sh
scp deploy/backup/ntu-shower-backup.service deploy/backup/ntu-shower-backup.timer deploy@<deploy-host>:/etc/systemd/system/
ssh deploy@<deploy-host> "chmod 700 /opt/ntu-shower-backup/ntu-shower-backup.sh && systemctl daemon-reload && systemctl enable --now ntu-shower-backup.timer"
```

## 要点（改配置前先读）

- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头
  （`Upgrade` + `Connection "upgrade"` + `proxy_http_version 1.1`）：
  前者服务用户页 `/api/v1/stream`，后者服务设备网关 `/gateway/device`。
- `proxy_read_timeout 3600s`：设备网关与实时推送都是长连接，默认 60s 会被 nginx 掐断。
- `.env`（数据库路径等生产配置）只在服务器上，不入仓库。
