# deploy — 部署资产

服务器运行配置的唯一事实来源。**改这里，再同步到服务器**，不要直接改服务器。

## 文件

| 文件 | 服务器位置 | 生效方式 |
|---|---|---|
| 根目录 `compose.yaml` | `/opt/ntu-shower/compose.yaml` | `NTU_SHOWER_TAG=<tag> docker compose -f /opt/ntu-shower/compose.yaml up -d` |
| `litestream.yml` | `/etc/ntu-shower/litestream.yml` | `docker compose restart litestream` |
| `nginx/ntu-shower.conf` | `/etc/nginx/sites-enabled/<public-domain>.conf`（**独立文件，非软链**：sites-available 与 enabled 是两份拷贝，只写 enabled 才生效） | `nginx -t && systemctl reload nginx` |
| `publish_apk.ps1` | **不上服务器**：本地工具 | `.\deploy\publish_apk.ps1 -Token <admin 的 API Bearer 凭据>` |

## 部署（标准 compose 命令，无自建脚本）

```bash
scp compose.yaml deploy@<deploy-host>:/opt/ntu-shower/compose.yaml
scp deploy/litestream.yml deploy@<deploy-host>:/etc/ntu-shower/litestream.yml
ssh deploy@<deploy-host> "cd /opt/ntu-shower && NTU_SHOWER_TAG=sha-<短哈希> docker compose up -d"
ssh deploy@<deploy-host> "curl -s http://127.0.0.1:8110/healthz"
```

## 备份与恢复（LiteStream，litestream.io v0.3）

```bash
ssh deploy@<deploy-host> "docker exec ntu-shower-litestream litestream replicas -config /etc/ntu-shower/litestream.yml"
ssh deploy@<deploy-host> "docker run --rm -v /var/lib/ntu-shower:/var/lib/ntu-shower -v /var/backups/ntu-shower/litestream:/var/backups/ntu-shower/litestream litestream/litestream:0.3 restore -o /var/lib/ntu-shower/ntu-shower.db file:///var/backups/ntu-shower/litestream"
```

## 要点（改配置前先读）

- 镜像 tag 由 `NTU_SHOWER_TAG` 注入，**禁止写死 latest**；回滚 = 重新 `up -d` 指向上一个 sha tag。
- `location /` 与 `location /gateway/` 都必须带 WebSocket 升级头；`proxy_read_timeout 3600s`——设备网关与实时推送是长连接。
- `limit_req_zone` 的 zone 名 `api` 与其它站点共用 http 上下文，不得与其它站点重名。
- `.env`（生产配置）只在服务器上（`/etc/ntu-shower/ntu-shower.env`，600），不入仓库。
