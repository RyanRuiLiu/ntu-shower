# 0036. OTA 安装状态跟踪（防 force 死循环）

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

0.2.5 真机 OTA 出现死循环：force 发布无视版本号，设备安装重启后
hello_ack 仍提示同一版本可升级 → 再次下载安装。根因：force 语义缺少
"每设备安装确认"状态。另发现 init_tables 的 DROP TABLE releases
调试残留导致每次服务重启清空发布注册表。

## 考虑过的选项

* 设备端忽略相同版本（治标，服务端状态仍错）
* 服务端安装状态跟踪 + roll-forward（行业标准，Play Console 同款语义）

## 决策结果

- release_install 表：设备 hello 时的 version_code 即安装确认，
  ≤ 该版本的发布自动标记已安装；
- latest_for：force 发布已确认则不再推；rearm 端点清除确认供调试重推；
- delete 端点下架发布（紧急止损）；移除 init_tables 的 DROP。

### 正面后果

* OTA 循环根除；重启/重部署不再丢发布
* 调试重推语义保留（rearm）

### 负面后果

* 重推同一版本多一步 rearm 调用

## 相关

- docs/decisions/0020-ota-push-and-force-release.md
