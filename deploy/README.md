# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| 根目录 `compose.yaml` | `/opt/ntu-shower/compose.yaml` | `NTU_SHOWER_TAG=<tag> docker compose -f /opt/ntu-shower/compose.yaml up -d` |
| `litestream.yml` | `/etc/ntu-shower/litestream.yml` | `docker compose restart litestream` |
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-enabled/<public-domain>.conf`（**独立文件，非软链**：sites-available 与 enabled 是两份拷贝，只写 enabled 才生效） | 见下方「nginx 同步程序」（reload 在本机曾静默失效，以 restart 恢复） |
| `publish_apk.ps1` | **不上服务器**：本地工具 | `.\deploy\publish_apk.ps1 -Token <admin 的 API Bearer 凭据>` |

## 部署（自动为主，手动恢复）

**自动路径（默认）**：main 合并 → `docker` workflow 构建推送镜像 → `deploy` job 以专用部署密钥
（GitHub secrets `DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_USER`，公钥在服务器 authorized_keys，注释
`ntu-shower-deploy-ci`）执行 pull → `up -d` → healthz 探测（最长 60 秒，失败即 CI 红）。部署序列
与下方手动步骤一致，只是由 CI 代为执行。

**手动路径（回滚、CD 不可用时）**：

```bash
scp compose.yaml <deploy-user>@<deploy-host>:/opt/ntu-shower/compose.yaml
scp deploy/litestream.yml <deploy-user>@<deploy-host>:/etc/ntu-shower/litestream.yml
ssh <deploy-user>@<deploy-host> "cd /opt/ntu-shower && NTU_SHOWER_TAG=sha-<短哈希> docker compose pull ntu-shower && NTU_SHOWER_TAG=sha-<短哈希> docker compose up -d"
ssh <deploy-user>@<deploy-host> "curl -s http://127.0.0.1:8110/healthz"
```

## nginx 同步程序（2026-11-17 实测修订）

```bash
# 1. 仓库事实源 → 真实值（占位符 <public-domain>/<deploy-host> 填服务器实值），scp 到 /tmp
# 2. 备份现网配置——必须落在 sites-enabled 之外（include 通配会把备份一并加载）
ssh <deploy-user>@<deploy-host> "mkdir -p /var/backups/manual/nginx && cp /etc/nginx/sites-enabled/<public-domain>.conf /var/backups/manual/nginx/conf.bak-$(date +%Y%m%d)"
# 3. 替换 + 语法测试 + 重载
ssh <deploy-user>@<deploy-host> "cp /tmp/ntu-conf-new.conf /etc/nginx/sites-enabled/<public-domain>.conf && nginx -t && (systemctl reload nginx || systemctl restart nginx)"
```

**生效必须用行为探针证明**，`nginx -t` 通过与 reload 退出码都不构成证据——本机曾出现
reload 与直接 HUP 均返回成功、worker 进程却未更换的静默失效（2026-11-17 实证，改
`systemctl restart` 解决，原因未明）：

```bash
ssh <deploy-user>@<deploy-host> "ps -o pid,lstart -C nginx --no-headers"   # worker 启动时间应为刚才
# 限速变更：洪泛探针对照 burst 数字（如登录 1r/s burst=5 + 12 并发 = 6 放行 6 拒绝）
```

失败回滚：把备份 `cp` 回原位并 `systemctl restart nginx`。

## 备份与恢复（LiteStream，litestream.io v0.3）

```bash
ssh <deploy-user>@<deploy-host> "docker exec ntu-shower-litestream litestream replicas -config /etc/ntu-shower/litestream.yml"
ssh <deploy-user>@<deploy-host> "docker run --rm -v /var/lib/ntu-shower:/var/lib/ntu-shower -v /var/backups/ntu-shower/litestream:/var/backups/ntu-shower/litestream litestream/litestream:0.3 restore -o /var/lib/ntu-shower/ntu-shower.db file:///var/backups/ntu-shower/litestream"
```

## 要点（改配置前先读）

- 镜像 tag 由 `NTU_SHOWER_TAG` 注入，**禁止写死 latest**；回滚 = 重新 `up -d` 指向上一个 sha tag。
- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头；`proxy_read_timeout 3600s`——设备网关与实时推送是长连接。
- `limit_req_zone` 的 zone 名 `api` 与其它站点共用 http 上下文，不得与其它站点重名。
- `.env`（生产配置）只在服务器上（`/etc/ntu-shower/ntu-shower.env`，600），不入仓库。
