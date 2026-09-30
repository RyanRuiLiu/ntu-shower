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
        versionCode = 10
        versionName = "0.3.3"
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
        // /sdcard/ 硬编码：AppConfig 是目标设备的环境锚点（实测路径，见 ADR-0011/0022），
        // Environment.getExternalStorageDirectory() 已废弃且返回同一位置
        disable += "SdCardPath"
        // ANDROID_ID 即设备身份：设计决策（ADR-0004），多设备注册表的唯一键
        disable += "HardwareIds"
        // okhttp 钉在 3.12.13：4.x 起要求 API 21，设备冻结 4.4（见 docs/ARCHITECTURE.md）
        disable += "NewerVersionAvailable"
    }

}

// JVM 单测覆盖率范围：只排除**真正无法在 JVM 上覆盖**的类。
// 原则：能通过抽取纯逻辑覆盖的，就不排除——赦免清单越短，门越有意义。
// 逐条理由见 docs/STANDARDS.md §设备端测试。
val frameworkBoundClasses = listOf(
    // 应用入口与常驻服务：依赖 Looper / 系统服务
    "**/ShowerApp*",
    "**/service/**",
    "**/receiver/**",
    // 网络栈：依赖真实 TLS/DNS 套接字（纯逻辑部分已抽为 ReconnectBackoff/DnsResolver 报文层）
    "**/transport/GatewayClient*",
    "**/core/net/HttpClientFactory*",
    "**/core/tls/**",
    "**/core/identity/**",
    // root shell 进程管理：依赖真实 su 进程（输出解析已抽为 ShellOutput 并纳入覆盖）
    "**/core/shell/RootShell*",
    "**/core/perf/**",
    "**/core/config/AppConfig*",
    // 无障碍节点操作：依赖 android.view.accessibility 节点树与窗口管理器
    // （CommandScheduler 只剩线程编排，其串行决策已抽为 SerialCommandQueue 并纳入覆盖）
    "**/features/automation/AutomationFlow*",
    "**/features/automation/BathAppNavigator*",
    "**/features/automation/CommandScheduler*",
    "**/features/automation/CoverOverlay*",
    "**/features/automation/PhotoCapture*",
    "**/features/automation/AssignAction*",
    "**/features/automation/SettleAction*",
    // 设备 IO：ADB 字节泵 / MediaCodec / FileObserver（事件过滤已抽为 DoorEventFilter）
    "**/features/remote/AdbBridge*",
    "**/features/remote/ScreenCapture*",
    "**/features/doors/DoorMonitor*",
    "**/features/doors/DoorSnapshotReader*",
    "**/features/doors/DoorSnapshotSender*",
    // 自管理：依赖 root shell 与包管理器（设置合并不依赖，已纳入覆盖）
    "**/features/selfmanage/A11yEnabler*",
    "**/features/selfmanage/OtaUpdater*",
    // 纯数据载体：仅编译器生成的 equals/hashCode/toString/copy/values/组件 N，
    // 无手写逻辑（JaCoCo 官方建议排除生成代码）。业务逻辑仍在其中方法之外。
    "**/features/automation/PageState*",
    "**/features/automation/CommandOutcome*",
    "**/features/automation/FlowError*",
    "**/transport/Envelope*",
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

// 覆盖率门：赦免清单之外的指令覆盖率不得低于 95%（当前 98.7%，门槛只升不降）。
// 提高门槛的前提是先把逻辑抽出来测，而不是放宽统计范围。
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
                minimum = "0.95".toBigDecimal()
            }
        }
    }
}
