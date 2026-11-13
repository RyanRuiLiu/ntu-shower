# ADR-0022: 安卓依赖冻结在 API 19 兼容线

- 状态：Accepted
- 日期：2026-11-13
- 决策者：项目负责人
- 取代：无

## 背景

设备 ROM 冻结 Android 4.4（API 19），不进 Google Play（OTA 自托管），`minSdk = targetSdk = 19`
是运行时下限，不可动。Android 生态主线均与此冲突：okhttp 4.x 起要求 API 21；
conscrypt-android 2.7.0/2.8.0/2.9.0 抬高 minSdk（PR #32/#14/#30 的 manifest 合并冲突实测，
android 门禁红）；AndroidX 系依赖在 targetSdk 19 的旧 ROM 上无验证条件。

项目运行时依赖只有 okhttp 与 conscrypt（`android/app/build.gradle.kts`），不含任何
AndroidX/support 库；`android.useAndroidX=false` 维持构建语义与现网一致，并作为护栏
防止未来误引入 AndroidX 系依赖。

## 决策

- `android.useAndroidX=false`：构建语义冻结，见上。
- okhttp 钉 3.12.13：API 19 上可用的最后一条线（PR #19 据此关闭）。
- conscrypt-android 钉 2.5.x（当前 2.5.3，PR #33）：TLS 供应商兼容线，2.6 起不可用。
- 升级上限由 `dependabot.yml` 的 ignore 规则编码（okhttp `>=3.13`、conscrypt `>=2.6`），
  机器执行，不靠口头约定。

## 备选方案

- 迁移 AndroidX：未选择——设备固件不可升级，收益为零；androidx 多数库虽声明
  minSdk 14/19，但 targetSdk 19 + 旧 WebView/无障碍栈的回归面无法在真机之外验证。
- 升级 okhttp 到 4.x：未选择——API 21 下限，直接不可运行。

## 后果

- 正面：依赖行为与现网设备二进制一致；升级噪音被 ignore 规则消音（不再重复开 PR）。
- 负面：与 Android 生态主线存在经记录的偏离；钉死的版本不再有上游安全更新——
  缓解面是设备端单机使用、不处理不可信网络输入（TLS 供应商已由 conscrypt 提供 AEAD）。
- 需要监控的风险：设备换型/ROM 升级、真机出现 TLS 或兼容性事故——任一发生即重启本决策。

## 验证

`./gradlew assembleDebug` 门禁绿（CI android job，覆盖率 ≥98%）；dependabot 不再为
上述依赖开升级 PR（2026-11-13 起 ignore 规则生效）。

---
