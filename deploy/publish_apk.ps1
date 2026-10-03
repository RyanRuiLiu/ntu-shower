# 发布 APK 到 OTA。版本号只有一个来源：`android/app/build.gradle.kts`。
#
# 为什么不手填：OTA 覆盖安装要求新旧版本可比，而"发布时人工填一遍"就是把手里的值
# 复制一次——复制没有终点，漏改一次的表现是设备不升级或反复升级，都偏静默。
# RUNBOOK 里原先那条 curl 就是手填的，这个脚本取代它。
#
# 用法：
#   .\deploy\publish_apk.ps1 -Identity <admin身份标识>
#   .\deploy\publish_apk.ps1 -Identity <admin身份标识> -SkipBuild   # 用已构建的 APK
#
# 前提：`android/gradle.properties` 里有 serverDomain 与签名口令（见该文件的 example）。

param(
    [Parameter(Mandatory = $true)][string]$Identity,
    [switch]$SkipBuild,
    [string]$Target = "all",
    [string]$BaseUrl = "https://ntu-shower.iseekdream.com"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
$GradleFile = Join-Path $RepoRoot "android/app/build.gradle.kts"
$ApkPath = Join-Path $RepoRoot "android/app/build/outputs/apk/release/app-release.apk"

# ---- 版本：从 build.gradle.kts 读，不另存一份 ----
if (-not (Test-Path $GradleFile)) { throw "找不到 $GradleFile" }
$gradle = Get-Content $GradleFile -Raw
$codeMatch = [regex]::Match($gradle, 'versionCode\s*=\s*(\d+)')
$nameMatch = [regex]::Match($gradle, 'versionName\s*=\s*"([^"]+)"')
if (-not $codeMatch.Success -or -not $nameMatch.Success) {
    throw "从 $GradleFile 读不出 versionCode / versionName——正则与文件格式脱节了，先修这里"
}
$versionCode = $codeMatch.Groups[1].Value
$versionName = $nameMatch.Groups[1].Value
Write-Host "版本（取自 build.gradle.kts）：$versionName ($versionCode)" -ForegroundColor Cyan

# ---- 构建 ----
if (-not $SkipBuild) {
    Write-Host "构建 release APK..." -ForegroundColor Green
    Push-Location (Join-Path $RepoRoot "android")
    try {
        & .\gradlew.bat detekt :app:lintDebug :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification assembleRelease --console=plain
        if ($LASTEXITCODE -ne 0) { throw "构建或门禁未通过，未发布" }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $ApkPath)) { throw "找不到 $ApkPath（去掉 -SkipBuild 重新构建）" }

# ---- 发布 ----
Write-Host "上传到 $BaseUrl ..." -ForegroundColor Green
$response = curl.exe -s -X POST "$BaseUrl/api/v1/ota/releases" `
    -H "X-Identity: $Identity" `
    -F "version=$versionName" `
    -F "version_code=$versionCode" `
    -F "target=$Target" `
    -F "file=@$ApkPath"

if ($LASTEXITCODE -ne 0) { throw "上传失败" }
Write-Host $response

# 服务端返回 notified_devices，非空即表示在线设备已收到升级通知并会自行安装重连。
$parsed = $response | ConvertFrom-Json
if ($parsed.notified_devices) {
    Write-Host "已通知 $($parsed.notified_devices.Count) 台在线设备" -ForegroundColor Green
} else {
    Write-Host "没有在线设备收到通知（设备离线时可稍后由它自己拉取 manifest）" -ForegroundColor Yellow
}
