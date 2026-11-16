# 安全政策

## 报告漏洞

不要以公开 Issue、PR 或 Discussion 披露安全漏洞。

使用 GitHub 的私密漏洞报告（仓库 Security 标签页 → Report a vulnerability）提交，仅维护者可见。报告请包含：受影响的端点或组件、复现步骤或概念验证、影响评估。收悉后 72 小时内确认，确认后协商修复窗口，修复发布前不公开细节。

修复发布后在 CHANGELOG 的 Security 分类登记；如报告者愿意，一并致谢。

## 支持范围

main 分支的最新发布版本（版本序列见 [CHANGELOG.md](CHANGELOG.md)）。更早的版本不接收安全修复。

## 设计立场

安全不依赖设计保密：协议（`protocol/schema/`）与契约（`/openapi.json`）即对外文档，公开是常态。凭据与生产标识不进仓库——提交历史与现行树由 gitleaks 与仓库内扫描器双门禁守住，真实值由部署侧持有。

威胁模型、资产分级与信任边界见 [docs/security.md](docs/security.md)。
