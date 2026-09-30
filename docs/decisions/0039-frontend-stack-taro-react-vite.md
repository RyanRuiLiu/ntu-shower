# 0039. 前端技术栈：Taro + React + Vite，接受 Vite 4 约束

* 状态：superseded（**前端工程已删除**，见文末。服务端契约为任何前端保留）
* 日期：2026-09-30

## 背景与问题

前端要求**小程序与网页共用一套代码**（用户明确指定，不接受两套实现）。
这决定必须用跨端框架，而不是原生小程序 + 独立 H5。

## 考虑过的选项

* **Taro + React + Vite**（采纳）
* Taro + React + webpack5
* uni-app（Vue 3）
* 原生小程序 + 独立 H5 —— 直接排除：两套代码正是要避免的

## 决策结果

**Taro 4.3.0 + React 18.3.1 + TypeScript 5.9.3 + Sass + Vite**，pnpm 管理。

### 为什么是 Taro 而不是 uni-app

选 React 而非 Vue 的理由是**可测试性**：本项目服务端有覆盖率门（96%）、设备端有
（98%），前端若是唯一没有质量门的部分，就违背了整个项目的前提。自写的纯逻辑可以
用 vitest 直接测，而框架耦合的组件行为测不了——React + TypeScript 的纯函数边界
最清晰，这条路径有把握。

### 为什么是 Vite 而不是 webpack5

webpack5 的约束**比 Vite 更死**：

| | Vite | webpack5 |
|---|---|---|
| `@tarojs/*-runner` 的 peer | `vite: ^4`（范围） | `webpack: 5.91.0`（**精确钉死**） |
| 约束内可用的最新 | 4.5.14（4.x 末版） | 只有 5.91.0，而最新是 5.111.1 |
| runner 内部依赖 | `@vitejs/plugin-legacy@^4` | `babel-loader@8.2.1`（2021）、`webpack-dev-server@^4`（当前 v5）、`webpackbar@^5`（当前 v7） |

### Vite 被锁在 4，这是 Taro 的技术债

* `@tarojs/vite-runner` 从 4.0 到 4.3（含 `4.3.1-alpha`）**全线声明 `vite: ^4`**
* Taro 在本仓库的 issue 中已知此事近两年：
  [#16994](https://github.com/NervJS/taro/issues/16994)（2024-12，标题即「插件锁定 vite 版本为 4」，
  被 PR #17312 关闭，但修的是插件版本匹配而非升级 Vite）、
  [#18639](https://github.com/NervJS/taro/issues/18639)（2025-11，同样问题再现，**至今 open**）
* 这是**有意为之或优先级问题，不是维护停滞**：Taro 4.3.0 发布于决策当日（2026-09-29），
  仓库几乎每日有提交，PR 带完整测试

**对安全的实际影响为零**：Vite 4.5.11 已修复 CVE-2025-31125（dev server 的 `?inline&import`
文件泄漏，仅影响 `--host` 暴露 dev server 的场景），而本项目用 **4.5.14**，在其之后。
Vite 官方政策是「只保最近一个主版本，更早的仅在安全问题时才更新」——4.5.x 仍在收安全补丁。

### 约束内取最新

「能新则新」在此约束下的正确形式是取每个 peer 允许的最大值，已执行：

* Vite **4.2.0 → 4.5.14**（同时修掉 Windows 上必崩的 bug：4.2 的 `tryNodeResolve`
  把 Rollup 虚拟模块 ID 的 `\0` 前缀当路径传给 `fs.realpathSync`）
* `@vitejs/plugin-react` **4.3.0 → 5.2.0**（peer 为 `vite ^4.2||^5||^6||^7||^8`）
* React **18.0.0 → 18.3.1**（满足 `react-reconciler@0.29` 的 `^18.2.0`）
* TypeScript **5.4.5 → 5.9.3**、ESLint **8.57.0 → 8.57.1**（`eslint-config-taro` 锁 `^8`）
* vitest **3.2.4**（5.0.3 要求 `vite ^6.4`，不可用；vitest 自带 Vite 5，与项目 Vite 4 由 pnpm 隔离）

### 正面后果

* 一套代码覆盖小程序与 H5，符合最初的硬要求
* 前端与另两端一样有质量门（`tsc --noEmit` 严格模式、eslint、stylelint、vitest 覆盖率）
* 依赖全部取自官方模板与官方包，无自造脚手架

### 负面后果

* **被锁在 Vite 4**：用不上 Vite 5+ 的特性与性能改进（本项目规模下无实际影响）
* **无法升 React 19**：`@tarojs/react@4.3.0` 的 peer 是 `react ^18`
* **无法升 ESLint 9+**：`eslint-config-taro` 的 peer 是 `eslint ^8`

## 复查触发条件

出现以下任一条时重新评估，**否则不因"版本旧"而纠结**：

1. Taro 支持 Vite 5+ → **升级**，不是换框架
2. Vite 4 出现影响 **build 产物**（而非 dev server）的漏洞 → 立即处理
3. 需要 Taro 不支持的端（支付宝小程序、RN 等）→ 那时再评估框架选型
4. Vite 4 的某项限制确实挡住需求 → 目前无此情况

## 后续：前端工程已删除

技术选型本身未走到需要推翻的一步，但**工程最终被移除**——投入产出不成比例：
跨端框架的接入成本（Vite 版本约束、组件库与测试环境的摩擦、两端构建差异）
相对于本项目所需的两个页面而言过高。用户端沿用既有的零构建静态页即可。

**这次尝试的产出没有浪费**，以下改进独立于前端而保留：

* **`DoorState` 枚举**（`server/app/modules/doors/schemas.py`）：把喷头状态从裸字符串
  变成受约束的类型，让 OpenAPI 能生成联合类型。这是**契约质量的提升**，
  任何客户端都受益
* **`Problem.title` 中文**（`server/app/core/problems.py`）：服务端直接返回可展示的
  文案，不再需要客户端做一层翻译
* **`docs/CONTENT.md`**：设备端与服务端的术语统一

因此本条记录保留：它解释了「服务端为何有这些面向客户端的设施」，
也避免将来重新评估时再踩一遍 Vite 版本约束与组件库接入的坑。

## 相关

- `docs/CONTENT.md` · `server/app/modules/doors/schemas.py` · `server/app/core/problems.py`
