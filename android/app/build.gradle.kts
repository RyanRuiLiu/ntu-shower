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
    // 格式类规则（ktlint）自动修正源码
    autoCorrect = true
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
        versionCode = 8
        versionName = "0.3.1"
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

// JVM 单测覆盖率报告：统计纯逻辑类；框架耦合类（依赖 android.jar 桩/真实设备/
// Looper 线程）无法在 JVM 上有意义地覆盖，赦免清单见 docs/STANDARDS.md §设备端测试
// 赦免边界（依据 docs/STANDARDS.md §设备端测试）：
// - 框架耦合类：依赖 android.jar 桩/真实设备/Looper 线程，JVM 上无法有意义地覆盖；
// - 常量类：仅字段常量，无可执行逻辑；
// - 网络 IO（DnsResolver.query）：需真实 DNS 报文交互，构造/解析纯函数已有单测。
val frameworkBoundClasses = listOf(
    "**/ShowerApp*",
    "**/service/*",
    "**/receiver/*",
    "**/transport/GatewayClient*",
    "**/core/net/*",
    "**/core/tls/*",
    "**/core/identity/*",
    "**/core/shell/*",
    "**/core/perf/*",
    "**/core/config/AppConfig*",
    "**/features/automation/*",
    "**/features/remote/AdbBridge*",
    "**/features/remote/ScreenCapture*",
    "**/features/doors/DoorMonitor*",
    "**/features/doors/DoorSnapshotReader*",
    "**/features/doors/DoorSnapshotSender*",
    "**/features/selfmanage/*",
    "**/core/config/AppConfig*",
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

// 覆盖率门：赦免清单之外的纯逻辑指令覆盖率不得低于 80%（随测试演进只升不降）
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
                minimum = "0.80".toBigDecimal()
            }
        }
    }
}
