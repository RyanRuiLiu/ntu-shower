import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("dev.detekt")
    jacoco
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_22)
        // 警告即错误：警告堆积是代码漂移的起点，也让"统一"无从谈起。
        allWarningsAsErrors.set(true)
    }
}

detekt {
    buildUponDefaultConfig = true
    config.setFrom(file("$rootDir/detekt.yml"))
}

tasks.withType<dev.detekt.gradle.Detekt>().configureEach {
    // 不自动改源码：构建过程静默修改文件会让"改动来自谁"变得不可追溯。
    // 需要格式化时显式执行 `gradlew detekt --auto-correct`。
    autoCorrect = false
}

android {
    namespace = "com.ryanliu.ntushower"
    compileSdk = 37

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_22
        targetCompatibility = JavaVersion.VERSION_22
    }

    defaultConfig {
        applicationId = "com.ryanliu.ntushower"
        minSdk = 19          // 设备 ROM 固定 Android 4.4，运行时下限不可动
        targetSdk = 19       // 目标设备冻结在 4.4，无升级路径
        versionCode = 104
        versionName = "0.5.3"
        buildConfigField("String", "SERVER_DOMAIN", "\"${providers.gradleProperty("serverDomain").get()}\"")
        buildConfigField("String", "APP_VERSION", "\"$versionName\"")
        buildConfigField("int", "VERSION_CODE", "$versionCode")
    }

    signingConfigs {
        create("release") {
            storeFile = rootProject.file("../config/keystore/ntu-shower.keystore")
            storePassword = providers.gradleProperty("keystorePassword").get()
            keyAlias = providers.gradleProperty("keystoreAlias").get()
            keyPassword = providers.gradleProperty("keystorePassword").get()
        }
    }

    buildTypes {
        debug {
            enableUnitTestCoverage = true  // JVM 单测 JaCoCo 覆盖率（报告任务见下）
        }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    lint {
        // 设备 ROM 冻结在 Android 4.4 且不进 Google Play：targetSdk 19 是有意决策
        disable += "ExpiredTargetSdkVersion"
        // /sdcard/ 硬编码：AppConfig 是目标设备的环境锚点（实测路径，见 docs/DECISIONS.md），
        // Environment.getExternalStorageDirectory() 已废弃且返回同一位置
        disable += "SdCardPath"
        // ANDROID_ID 即设备身份：设计决策（docs/DECISIONS.md），多设备注册表的唯一键
        disable += "HardwareIds"
        // okhttp 钉在 3.12.13：4.x 起要求 API 21，设备冻结 4.4（见 docs/ARCHITECTURE.md）
        disable += "NewerVersionAvailable"
    }

    testOptions {
        // 未 mock 的 Android 框架方法返回默认值而不是抛异常。
        //
        // 我们测的是"抽出来的纯逻辑"，但那条路径上难免擦到 Logger 或系统时钟——那些类在
        // JVM 里只有 stub，默认行为是抛 RuntimeException。让它们返回默认值，测的仍然是
        // 被测逻辑本身的分支与结果，而不是"某个框架调用恰好被 mock 了没有"。
        //
        // 代价是它也掩盖了"本不该调用框架方法"这类问题。因此纳入覆盖的类要经得起看：
        // 真正依赖框架的那些已经列在 frameworkBoundClasses 里，不参与覆盖率门禁。
        unitTests.isReturnDefaultValues = true
    }

}

// JVM 单测覆盖率范围：只排除**真正无法在 JVM 上覆盖**的类。
// 原则：能通过抽取纯逻辑覆盖的，就不排除——赦免清单越短，门越有意义。
// 逐条理由见 AGENTS.md §设备端测试。
val frameworkBoundClasses = listOf(
    // 应用入口与常驻服务：依赖 Looper / 系统服务
    "**/ShowerApp*",
    "**/service/**",
    "**/receiver/**",
    // 主线程投递：两个短路分支（当前已是主线程）必须真实 Looper 才走得到，
    // 纯 JVM 测不出；等待逻辑本身是 JDK 的 CountDownLatch，无自研分支
    "**/core/threading/**",
    // 网络栈：依赖真实 TLS/DNS 套接字（纯逻辑部分已抽为 ReconnectBackoff/DnsResolver 报文层）
    "**/transport/GatewayClient*",
    "**/core/net/HttpClientFactory*",
    "**/core/tls/**",
    "**/core/identity/**",
    // root shell 进程管理：依赖真实 su 进程（输出解析已抽为 ShellOutput 并纳入覆盖）
    "**/core/shell/RootShell*",
    "**/core/shell/ShellChannel*",
    "**/core/perf/**",
    "**/core/config/AppConfig*",
    // 数据库归档的 shell/文件系统编排（mv/rm 经 root 通道、/sdcard 实盘）；判定逻辑
    // 已抽为 BathDbArchivePolicy 并纳入覆盖
    "**/features/selfmanage/BathDbArchiver*",
    // 日志上送的文件系统/网络编排（读 /sdcard 实盘、经 GatewayClient 发送）
    "**/features/selfmanage/LogShipper*",
    // 无障碍节点操作：依赖 android.view.accessibility 节点树与窗口管理器
    // （CommandScheduler 只剩线程编排，其串行决策已抽为 SerialCommandQueue 并纳入覆盖）
    "**/features/automation/AutomationFlow*",
    "**/features/automation/BathAppNavigator*",
    // 页面状态源：按 ID 查控件要走无障碍系统服务，系统类搬不进 JVM。
    // 判定规则已抽为 PageRules / PageProbe 并由 BathAppPageTest 覆盖；
    // A11yProbe 是它们的系统侧实现，PageElement 是它返回的数据载体
    "**/features/automation/BathAppPage*",
    "**/features/automation/A11yProbe*",
    "**/features/automation/PageElement*",
    "**/features/automation/CommandScheduler*",
    "**/features/automation/CoverOverlay*",
    // 屏幕提示：窗口与主线程状态机依赖 WindowManager / Handler / 系统服务；
    // 判定逻辑在 NoticeRule 里并有单测，不随这两个外壳一起豁免
    "**/features/notice/NoticeOverlay*",
    "**/features/notice/NoticeController*",
    // 语音播报：下载/播放/后台预下载/音量守卫依赖 OkHttp 文件系统、线程、MediaPlayer/
    // AudioManager、SharedPreferences；参数、音量计划、缓存淘汰已抽为纯逻辑并纳入覆盖
    "**/features/voice/VoiceClipStore*",
    "**/features/voice/VoicePlayer*",
    // MediaCodec/MediaExtractor 解码外壳；纯拼流、静音、淡入与格式校验由
    // VoicePcmAssemblerTest 覆盖
    "**/features/voice/VoicePcmDecoder*",
    "**/features/voice/VoicePrefetch*",
    "**/features/voice/VoiceVolumeGuard*",
    // 纯数据载体：只有编译器生成的方法；业务逻辑在 VoiceClipSpec/VoiceVolume/
    // VoiceClipCachePolicy 中并有单测
    "**/features/voice/VoiceResult*",
    "**/features/voice/VoiceVolumePlan*",
    "**/features/voice/VoiceClipFile*",
    // 自更新：安装/退避/尝试计数依赖 root shell、文件系统与设备日志，纯逻辑部分
    // （版本比对、sha 校验）随集成路径验证
    "**/features/selfmanage/OtaUpdater*",
    // 提示判定的输入/输出载体：只有编译器生成的方法；判定本身由 NoticeRuleTest 覆盖
    "**/features/notice/NoticeSettings*",
    "**/features/notice/LocalMoment*",
    "**/features/notice/NoticeDecision*",
    // 界面操作原语（自 service/ 迁入）：ShellUiChannel 依赖真实 su 进程与系统 Resources，
    // A11yUiChannel / UiTreeCodec 依赖无障碍节点树与窗口管理器
    "**/features/automation/A11yUiChannel*",
    "**/features/automation/ShellUiChannel*",
    "**/features/automation/UiTreeCodec*",
    "**/features/automation/PhotoCapture*",
    "**/features/automation/AssignAction*",
    "**/features/automation/SettleAction*",
    // 设备 IO：ADB 字节泵 / MediaCodec / FileObserver（事件过滤已抽为 DoorEventFilter）
    "**/features/remote/AdbBridge*",
    "**/features/remote/ScreenCapture*",
    "**/features/doors/DoorMonitor*",
    "**/features/doors/DoorSnapshotReader*",
    "**/features/doors/DoorSnapshotSender*",
    // 一次读数的载体；占用判据与完整性计算在 DoorRaw 里并有单测
    "**/features/doors/DoorReading*",
    // 应用私有库：两个类拿的都是 android.database 的 SQLiteDatabase 与 Cursor，JVM 里只有桩。
    // 它们做的事就是建表与两条 SQL，没有可以独立验证的算法——抽一层"行与 JSON 的转换"出来
    // 只会为了被测而存在。
    "**/core/storage/**",
    "**/core/config/ConfigCache*",
    // 自管理：依赖 root shell 与包管理器（A11ySettingsMerge / A11yState / TimeSync 的
    // shellDatetime 与阈值判定是纯逻辑，已纳入覆盖）
    "**/features/selfmanage/A11yGuard*",
    "**/features/selfmanage/OtaUpdater*",
    "**/features/selfmanage/TimeSync*",
    // 浴室 App 进程级重启：依赖真实 root shell（拉起与否的决策由 BathAppGuardTest 覆盖）
    "**/features/automation/BathAppRestart*",
    // MCU 直写：依赖真机 .so（libposix 的 JNI 绑定由类名决定，JVM 单测无法加载）。
    // 延时换算是纯逻辑（delayMinutes / parseMinuteOfDay），由 McuPowerScheduleTest 覆盖
    "**/features/selfmanage/McuPowerSchedule*",
    "**/com/xboot/stdcall/*",
    // 纯数据载体：仅编译器生成的 equals/hashCode/toString/copy/values/组件 N，
    // 无手写逻辑（JaCoCo 官方建议排除生成代码）。业务逻辑仍在其中方法之外。
    "**/features/automation/PageState*",
    "**/features/automation/PageSnapshot*",
    "**/features/automation/PageInfo*",
    "**/features/automation/CommandOutcome*",
    "**/features/automation/FlowError*",
    "**/transport/Envelope*",
    // 耗时记录：写 /sdcard 并打 Log，依赖真机环境（崩溃日志那套同类）。它的分段计算
    // 由 AutomationTraceTest 覆盖，守的是"记录器自己绝不能把流程搞挂"
    "**/features/automation/AutomationTrace*",
)

tasks.register<JacocoReport>("jacocoUnitTestReport") {
    dependsOn("testDebugUnitTest")
    reports {
        xml.required.set(true)
        html.required.set(true)
    }
    executionData(files(layout.buildDirectory.file("outputs/unit_test_code_coverage/debugUnitTest/testDebugUnitTest.exec")))
    sourceDirectories.setFrom(files("$projectDir/src/main/java"))
    classDirectories.setFrom(
        fileTree(layout.buildDirectory.dir("intermediates/built_in_kotlinc/debug/compileDebugKotlin/classes")) {
            exclude(frameworkBoundClasses)
        },
    )
}

dependencies {
    // API 19 上可用的最新 WebSocket 客户端（4.x 起要求 API 21，见 docs/ARCHITECTURE.md）
    implementation("com.squareup.okhttp3:okhttp:3.12.13")
    // 格式规则集（ktlint 规则镜像）：import 顺序/空白等格式强制，与服务端 ruff format 对位
    detektPlugins("dev.detekt:detekt-rules-ktlint-wrapper:2.0.0-alpha.6")
    testImplementation("junit:junit:4.13.2")
    // 本地单测使用真实 org.json 实现（android.jar 内为抛异常的桩）
    testImplementation("org.json:json:20260814")
}

// 覆盖率门：赦免清单之外的指令覆盖率不得低于 98%（当前 98.8%，门槛只升不降）。
// 提高门槛的前提是先把逻辑抽出来测，而不是放宽统计范围。
// 注意：以本任务报出的比值（排除后的 bundle）为准，jacoco 报告 XML 里的
// 全量计数略低——两者范围不同，别混用。
tasks.register<JacocoCoverageVerification>("jacocoUnitTestCoverageVerification") {
    dependsOn("jacocoUnitTestReport")
    executionData(files(layout.buildDirectory.file("outputs/unit_test_code_coverage/debugUnitTest/testDebugUnitTest.exec")))
    sourceDirectories.setFrom(files("$projectDir/src/main/java"))
    classDirectories.setFrom(
        fileTree(layout.buildDirectory.dir("intermediates/built_in_kotlinc/debug/compileDebugKotlin/classes")) {
            exclude(frameworkBoundClasses)
        },
    )
    violationRules {
        rule {
            limit {
                counter = "INSTRUCTION"
                value = "COVEREDRATIO"
                minimum = "0.98".toBigDecimal()
            }
        }
    }
}
