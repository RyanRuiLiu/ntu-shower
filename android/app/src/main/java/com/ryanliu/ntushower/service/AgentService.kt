package com.ryanliu.ntushower.service

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.ConfigCache
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.net.HttpClientFactory
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.CommandFactory
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.automation.CommandResultCache
import com.ryanliu.ntushower.features.automation.CommandScheduler
import com.ryanliu.ntushower.features.automation.CoverOverlay
import com.ryanliu.ntushower.features.doors.DoorMonitor
import com.ryanliu.ntushower.features.doors.DoorSnapshotSender
import com.ryanliu.ntushower.features.notice.NoticeController
import com.ryanliu.ntushower.features.remote.AdbBridge
import com.ryanliu.ntushower.features.selfmanage.A11yGuard
import com.ryanliu.ntushower.features.selfmanage.BathDbArchiver
import com.ryanliu.ntushower.features.selfmanage.LogShipper
import com.ryanliu.ntushower.features.selfmanage.OtaUpdater
import com.ryanliu.ntushower.features.selfmanage.PowerFacts
import com.ryanliu.ntushower.features.statusline.StatusLineController
import com.ryanliu.ntushower.features.voice.VoiceClipStore
import com.ryanliu.ntushower.features.voice.VoicePlayer
import com.ryanliu.ntushower.receiver.WatchdogReceiver
import com.ryanliu.ntushower.transport.Envelope
import com.ryanliu.ntushower.transport.GatewayClient
import com.ryanliu.ntushower.transport.MessageTypes
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * 设备主服务：无界面、后台常驻（START_STICKY）。
 *
 * 生命周期：开机拉起（本设备 = 无障碍重绑锚点；标准 ROM = BootReceiver）/
 * 覆盖安装自启（MY_PACKAGE_REPLACED）/ 看门狗定时拉活 / 崩溃自愈（ShowerApp）。
 * 多路径并发拉起经 onStartCommand 一次性初始化守卫收敛，无冲突。
 * 职责：网关长连接 + doors 采集 + 命令调度；后续功能在 features/ 中扩展并在此注册。
 */
// TooManyFunctions：拉起/配置/销毁三条生命周期路径合在一个类里是有意的——
// 它们操作同一组必须按序初始化与销毁的成员（连接、监视器、执行器、闹钟），
// 拆开反而要互相暴露状态。
@Suppress("TooManyFunctions")
class AgentService : Service() {

    private val worker = Executors.newSingleThreadExecutor()
    private val otaScheduler = Executors.newSingleThreadScheduledExecutor()

    /** 工作线程读盘完成后回到主线程刷新依赖配置的组件。 */
    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * 维护命令执行器。
     *
     * 独立于 [worker]：`device.exec` 会阻塞到命令结束（上限 25 秒），塞进 [worker]
     * 会连带把喷头状态采集与上报停掉同样久；塞进主线程更糟——心跳、看门狗广播、
     * 无障碍事件都在那儿排队（见 [dispatchCommand]）。
     */
    private val maintenanceExecutor = Executors.newSingleThreadExecutor()

    /**
     * 终结类维护动作（重启本程序、重启整机）的执行器。
     *
     * 与 [maintenanceExecutor] 分开，理由见 [DeviceMaintenance.isTerminating]：这两个动作的作用
     * 就是终止本进程，排在别的维护命令后面没有意义。真机上出过——一串 `device.exec` 各自等满
     * 25 秒，随后发的重启排在其后，服务端看到的仍是超时，而那一次重启其实一次都没执行。
     */
    private val terminatingExecutor = Executors.newSingleThreadExecutor()
    private var otaCheckFuture: ScheduledFuture<*>? = null
    private var otaUpdater: OtaUpdater? = null
    private var logShipFuture: ScheduledFuture<*>? = null
    private var lastLogShipAt = 0L
    private var lastCriticalMark = 0L
    private var lastHealthSnapshotAt = 0L

    @Volatile private var configRevision = 0

    private lateinit var client: GatewayClient
    private lateinit var sender: DoorSnapshotSender
    private lateinit var overlay: CoverOverlay
    private lateinit var notice: NoticeController
    private lateinit var statusLine: StatusLineController
    private lateinit var voicePlayer: VoicePlayer
    private lateinit var scheduler: CommandScheduler
    private var monitor: DoorMonitor? = null

    /** 已处理命令的结果缓存（幂等接收者，见 CommandResultCache）。 */
    private val recentOutcomes = CommandResultCache(COMMAND_DEDUP_MAX_COUNT)

    /** 设备统一日志（见 core.log.DeviceLog）：与服务端审计同事件族。 */
    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))
    private var adbBridge: AdbBridge? = null

    @Volatile private var trusted = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // onDestroy 之后同一实例再次收到 onStartCommand 属于异常生命周期：执行器已关闭，
        // 继续走初始化会抛 RejectedExecutionException。停止自身，让系统重建实例。
        if (worker.isShutdown) {
            Log.e(TAG, "onStartCommand after destroy; stopping service")
            stopSelf()
            return START_NOT_STICKY
        }
        // 每次拉起都核对无障碍链路：看门狗周期（WATCHDOG_INTERVAL_S）就是
        // 巡检周期。总开关被系统置 0 时本服务会失去无障碍这个自举锚点，看门狗的拉起
        // 是它仅剩的恢复机会——只在首次初始化时核对，这种情况下一次都不会执行。
        worker.execute {
            A11yGuard.ensureAvailable(deviceLog)
            // 修复过程本身会写 a11y.* 事件；通道已就绪就立刻送一份，不等周期
            if (::client.isInitialized && client.isReady()) {
                LogShipper.shipChanged(client)
            }
        }

        // 清掉上一世残留的命令 pid 文件。正常路径由 shell 包裹脚本自己删（见
        // ShellChannel.wrap），只有命令被异常打断（进程被杀、断电）才会留下——
        // 那种时刻没有任何人能替它收尾。启动时清扫是安全的：此刻没有任何命令在跑，
        // /data/local/tmp 里属于本程序的 pid 文件全部是死的。文件归 root 所有，
        // 走 root 通道删；不影响任何别的程序的同名空间。
        worker.execute { RootShell.cleanupAbandonedPidFiles() }

        if (!::client.isInitialized) {
            // 浴室 App 数据库攒到阈值就归档（见 BathDbArchiver）：开机是唯一确定没有
            // 会话进行的窗口，每晚断电重启给了它一天一次的机会
            worker.execute { BathDbArchiver.runIfNeeded() }

            // 先用上次落盘的那份配置：设备重启后要过 DNS、TLS 与首帧往返才拿得到新配置，
            // 中间那几秒（网络不通时则是一直）该用上次的值，而不是编译进 APK 的默认值。
            // 没有缓存（首次安装、或读不出来）时保持默认值，随后被 hello_ack 覆盖。
            //
            // 读盘不在主线程；网关也等它读完再连接——否则 hello_ack 可能先到，
            // 随后被这份旧缓存盖回去。
            worker.execute {
                ConfigCache.load(this@AgentService)?.let { RuntimeConfig.apply(it) }
                // TLS 上下文要读密钥库：在这里建客户端，连接本身仍回主线程发起
                client.warmUp()
                mainHandler.post {
                    if (worker.isShutdown) return@post
                    syncConfigDependents()
                    client.connect()
                }
            }
            // 先造遮盖层，再造就绪时读取遮盖状态的控制器，最后造读数发送器：
            // 遮盖层回调引用稍后初始化的 notice/statusLine，遮罩只在自动化流程中显示，
            // 构造期不会回调。
            overlay = CoverOverlay(this) { visible ->
                notice.onCoverVisibilityChanged(visible)
                statusLine.onCoverVisibilityChanged(visible)
            }
            notice = NoticeController(this, deviceLog) { overlay.isShowingOrPending }
            statusLine = StatusLineController(this, deviceLog) { overlay.isShowingOrPending }
            sender = DoorSnapshotSender(this) { reading ->
                notice.onReading(reading)
                statusLine.onReading(reading)
            }
            voicePlayer = VoicePlayer(
                this,
                // 单段下载 callTimeout 60s：播放命令另有总下载预算，预下载在独立后台线程，
                // 不占 voice 命令槽。缓存放应用私有 filesDir，不进 /sdcard；旧公共缓存由
                // store 初始化时删除。
                VoiceClipStore(
                    { HttpClientFactory.create(applicationContext, readTimeoutSeconds = 30, callTimeoutSeconds = 60) },
                    File(filesDir, "voice"),
                ),
                deviceLog,
            )
            scheduler = CommandScheduler(overlay, voicePlayer, onOutcome = { outcome -> sendOutcome(outcome) })
            client = GatewayClient(this, callback)
            // 连接由上面的缓存读取完成后发起：先拿到上次的配置，再接受 hello_ack
            worker.execute { voicePlayer.restorePendingVolume() }
            startDoorMonitor()
            startWatchdog()
            scheduleOtaCheck()
            startLogShipping()
            deviceLog.lifecycle("service.started", "action=${intent?.action ?: "startService"}")
            Log.i(TAG, "service started")
        }
        // 每次拉起都同步一次：看门狗 tick 也走这里，组件可能已被系统回收过
        syncConfigDependents()
        return START_STICKY
    }

    /** 上次上报的电源计划事实指纹；变化才重发（连上即报：初始为 null 时必发一次）。 */
    private var lastFactsHash: Int? = null

    private val callback = object : GatewayClient.Callback {
        override fun onConnected(trusted: Boolean, config: JSONObject, upgrade: JSONObject?, serverTime: String) {
            this@AgentService.trusted = trusted
            voicePlayer.setPrefetchAllowed(trusted)
            deviceLog.gateway(connected = true, detail = "trusted=$trusted")
            Log.i(TAG, "gateway connected, trusted=$trusted")
            // hello_ack 里的配置已经由 GatewayClient 写进 RuntimeConfig，这里做两件事：
            // 落到依赖配置的组件上，以及存一份供下次启动直接用。
            worker.execute { ConfigCache.save(this@AgentService, config) }
            syncConfigDependents()
            if (trusted) {
                // 日志上送（见 LogShipper）：离线期间的故障现场随回连自动到服务端
                worker.execute { LogShipper.ship(client) }
                worker.execute { reportFactsIfChanged() }
                worker.execute {
                    // 服务端的喷头现态可能已经被清空重建（开发期"删了重建"是常规操作），
                    // 而设备侧状态可能一直没变。先忘掉指纹，这次采集才会无条件入队，
                    // 把那批门重新推一遍；只补发积压是不够的，那时发件箱多半是空的。
                    //
                    // 标 startup：它是"当前全貌"，不是一次状态迁移，服务端据此只刷新
                    // 现态，不把它派生成事件与会话——网络抖动引起的重连不会污染统计。
                    //
                    // 积压的重发不在这里单独触发：captureAndEnqueue 末尾发的是发件箱里
                    // 最旧的一条，积压在前、这次采集在后，顺序天然是对的。
                    sender.forgetLastState()
                    sender.captureAndEnqueue(client, DoorSnapshotSender.SOURCE_STARTUP)
                }
            }
            if (upgrade != null) {
                Log.i(TAG, "upgrade available: ${upgrade.optString("version")}")
                worker.execute { otaUpdater?.checkAndInstall() }
            }
        }

        override fun onDisconnected(reason: String) {
            trusted = false
            voicePlayer.setPrefetchAllowed(false)
            statusLine.onDisconnected()
            deviceLog.gateway(connected = false, detail = reason)
            Log.w(TAG, "gateway disconnected: $reason")
        }

        override fun onMessage(envelope: Envelope) {
            when (envelope.type) {
                MessageTypes.DOOR_SNAPSHOT_ACK -> worker.execute {
                    sender.onAcked(envelope.payload.optString("snapshot_id"), client)
                }

                MessageTypes.DEVICE_COMMAND -> dispatchCommand(envelope)

                MessageTypes.CONFIG_UPDATE -> applyConfig(envelope)

                MessageTypes.STATUS_LINE_UPDATE -> statusLine.onStatus(envelope.payload)

                // 调试桥也要离开主线程：openAdbBridge 里的 setprop / stop adbd / start adbd
                // 是同步 shell 调用，closeAdbBridge 的关闭回调里还藏着一条——两条都会把
                // 主线程压住，连带排队心跳与无障碍事件。它们与 device.exec 共用执行器。
                //
                // 与 app.restart/device.reboot 不再互斥（那两个走 terminatingExecutor）：
                // 重启会打断正在进行的调试桥字节流，而重启的用途本就是"设备已经不通了"，
                // 那时打断一条调试桥不是代价。
                MessageTypes.BRIDGE_OPEN -> maintenanceExecutor.execute {
                    // 这两条不产生命令结果（服务端发出即返回，不等答复），但崩了必须留痕：
                    // 静默失败会让服务端一直等一个永远连不上来的设备，而日志里什么都没有
                    try {
                        openAdbBridge(envelope)
                    } catch (e: Throwable) {
                        Log.e(TAG, "bridge.open 失败：${e.message}", e)
                    }
                }

                MessageTypes.BRIDGE_CLOSE -> maintenanceExecutor.execute {
                    try {
                        closeAdbBridge("server requested")
                    } catch (e: Throwable) {
                        Log.e(TAG, "bridge.close 失败：${e.message}", e)
                    }
                }

                MessageTypes.OTA_UPDATE_AVAILABLE -> {
                    // 实时推送：立即触发一次检查（自动化期间 OtaUpdater 自行推迟）
                    worker.execute { otaUpdater?.checkAndInstall() }
                }
            }
        }
    }

    private fun dispatchCommand(envelope: Envelope) {
        val commandId = envelope.id
        // 幂等接收：发送方只发一次，接收方仍可能收到多次（重连重发、网络抖动）。
        // 分配/结算会真实操作硬件，重复执行必须在这里拦下；且重发结果而非静默忽略——
        // 服务端可能正是因为没收到结果才重发。
        recentOutcomes.recall(commandId)?.let { cached ->
            deviceLog.received(commandId, "(重复接收，重发既有结果)", duplicate = true)
            Log.i(TAG, "duplicate command, resending result: $commandId")
            sendOutcome(cached)
            return
        }
        val payload = envelope.payload
        val action = payload.optString("action", "")
        deviceLog.received(commandId, action)
        val params = payload.optJSONObject("params") ?: JSONObject()

        Log.i(TAG, "command: $action ($commandId)")
        // 按类别分发。三类互斥，顺序无副作用。
        //
        // 维护类必须挪出主线程：[dispatchCommand] 由 GatewayClient 投递到 main looper
        // （`handler.post { callback.onMessage(env) }`），而 execShell 会一直阻塞到命令
        // 结束。那段时间里心跳、看门狗广播、无障碍事件全部排队，设备在服务端看来像
        // 掉线——一条 `sleep 20` 的远程命令就能造成这个效果。
        // 过期判定交给调度器，用**相对时长**而不是绝对时刻。
        //
        // 设备没有 NTP，与服务器的墙上时钟可能差几分钟；拿本机时钟去比一个对端给出的
        // 时刻，会让"设备时钟走得快"这一件事把所有命令都判成已过期——而那与命令本身还
        // 值不值得执行毫无关系。这里把"还能等多久"换算成本机单调时钟上的一个点传下去，
        // 由调度器在真正开始执行前判：排队等待正是这条限制要管的场景。
        //
        // 用 rejected 而不是 failed：被拒的命令**没有产生任何副作用**，服务端据此可以
        // 安全重试；failed 意味着可能已经改了一半。
        val checkpoint = Uptime.nowMs()
        val timeoutMs = payload.optLong("timeout_ms", 0L)
        CommandFactory.create(action, params, commandId)?.let {
            scheduler.enqueue(it, checkpoint + timeoutMs)
            return
        }
        if (DeviceMaintenance.handles(action)) {
            val executor = if (DeviceMaintenance.isTerminating(action)) terminatingExecutor else maintenanceExecutor
            executor.execute {
                DeviceMaintenance.handle(action, params, commandId, checkpoint + timeoutMs, ::sendOutcome)
            }
            return
        }
        sendOutcome(
            CommandOutcome(commandId, OutcomeStatus.FAILED, ReasonCodes.PROTO_UNKNOWN_ACTION, "设备不认识这个操作：$action"),
        )
    }

    /**
     * 上报电源计划事实（device.status）：平台开关机时刻与本机时区。连上即报、变化才报
     * （指纹比对，与 LogShipper 同一手法）——服务端的 hold 对账与语音当地时刻全按它
     * 换算，服务端不定任何时刻。读不到（App 从未启动）不发：服务端按"计划未知"处理。
     *
     * **只在连接建立时读**（含重连与服务重启后的首次连接）：平台计划一天至多变一次，
     * 不跟着 60 秒看门狗 tick 轮询——那个 tick 只做幂等拉起，读一份几乎不变的文件
     * 每天 1440 次没有收益。计划若在连接存续期间变更，下一次重连或次日开机即刷新。
     */
    private fun reportFactsIfChanged() {
        if (!::client.isInitialized || !client.isReady()) return
        val facts = PowerFacts.read() ?: return
        if (facts.fingerprint() == lastFactsHash) return
        lastFactsHash = facts.fingerprint()
        client.send(MessageTypes.DEVICE_STATUS, org.json.JSONObject(facts.toPayload()))
        deviceLog.lifecycle(
            "power.facts-reported",
            "close=${facts.autoCloseTime} open=${facts.autoOpenTime} tz=${facts.zoneOffsetMinutes}",
        )
    }

    /**
     * 把 [RuntimeConfig] 的当前值同步到依赖配置的组件上。
     *
     * 三个入口都要调：启动时（此刻是登记表的默认值）、[onConnected]（hello_ack 带来的首份
     * 配置）、[applyConfig]（后续变更）。漏掉中间那个，服务端配置过的值就要等它碰巧再推一次
     * 变更才生效——而它可能整轮运行都不推。
     */
    private fun syncConfigDependents() {
        // 输出上限要在命令下发之前同步到通道上，之后设就晚了：读超了的部分已经在内存里
        RootShell.command.maxOutputBytes = DEVICE_EXEC_MAX_OUTPUT_BYTES
        recentOutcomes.setCapacity(COMMAND_DEDUP_MAX_COUNT)
        notice.onConfigChanged()
        statusLine.onConfigChanged()
        // hasReading 表示收到过完整读数：读失败不算，每次看门狗 tick 都会再补一次，
        // 直到读成为止。浴室数据库在没人洗浴时长期安静，事件驱动的采集补不上这次失败。
        // 两个浮层都需要占用时共用同一次读；一次回调会同时喂给它们。
        val noticeNeedsOccupancy = notice.needsOccupancy() && !notice.hasReading()
        val statusLineNeedsOccupancy = statusLine.needsOccupancy() && !statusLine.hasReading()
        if (noticeNeedsOccupancy || statusLineNeedsOccupancy) {
            worker.execute { sender.readAndNotify() }
        }
    }

    private fun applyConfig(envelope: Envelope) {
        val config = envelope.payload.optJSONObject("config") ?: JSONObject()
        val revision = envelope.payload.optInt("revision", 0)
        // 修订号单调守卫（AWS Shadow version 同型）：乱序的旧配置直接回执不应用——
        // 跨连接无序（新连接 hello_ack 与在途 push 竞争）时，后到的旧值会把新值抹掉
        if (revision <= configRevision && revision > 0) {
            client.send(MessageTypes.CONFIG_UPDATE_ACK, JSONObject().put("revision", revision), replyTo = envelope.id)
            deviceLog.lifecycle("config.stale", "revision=$revision 已应用 $configRevision，回执不应用")
            return
        }
        // 配置统一入口：全部键回写 RuntimeConfig，随后刷新依赖配置的组件并存一份给下次启动
        RuntimeConfig.apply(config)
        worker.execute { ConfigCache.save(this, config) }
        configRevision = revision
        syncConfigDependents()
        scheduleOtaCheck()
        // 看门狗间隔变更：重建监视器与看门狗
        startWatchdog()
        restartDoorMonitor()
        client.send(MessageTypes.CONFIG_UPDATE_ACK, JSONObject().put("revision", revision), replyTo = envelope.id)
        deviceLog.lifecycle("config.applied", "revision=$revision")
        Log.i(TAG, "config applied revision=$revision")
    }

    /**
     * 日志上送与健康记录的周期任务。
     *
     * 60 秒一跳：关键事件（warn/error）当跳就发；普通变化最多 5 分钟发一次；
     * 每 30 分钟写一条 health.snapshot，让"设备一直安静"与"上送链路坏了"可区分。
     * 文件没变时 LogShipper 不发，周期任务本身不产生流量。
     */
    private fun startLogShipping() {
        if (logShipFuture != null) return
        logShipFuture = otaScheduler.scheduleWithFixedDelay(
            { worker.execute { shipLogs() } },
            LOG_SHIP_TICK_S,
            LOG_SHIP_TICK_S,
            TimeUnit.SECONDS,
        )
    }

    private fun shipLogs() {
        if (!::client.isInitialized || !client.isReady()) return
        val now = Uptime.nowMs()
        val critical = DeviceLog.criticalMark()
        val criticalChanged = critical != lastCriticalMark
        if (!criticalChanged && now - lastLogShipAt < LOG_SHIP_PERIOD_MS) return
        if (now - lastHealthSnapshotAt >= LOG_HEALTH_SNAPSHOT_MS) {
            lastHealthSnapshotAt = now
            deviceLog.lifecycle(
                "health.snapshot",
                "a11y=${A11yRuntime.source != null} gateway=true version=${BuildConfig.APP_VERSION} " +
                    "config_revision=$configRevision",
            )
        }
        LogShipper.shipChanged(client, force = criticalChanged)
        lastLogShipAt = now
        lastCriticalMark = critical
    }

    /** 定时检查 OTA：间隔来自服务端配置，变更后重新调度（复用同一 updater 与 HTTP 客户端）。 */
    private fun scheduleOtaCheck() {
        otaCheckFuture?.cancel(false)
        if (otaUpdater == null) {
            otaUpdater = OtaUpdater(
                this,
                onProgress = { version, stage, detail ->
                    Log.i(TAG, "ota $version $stage $detail")
                    if (client.isReady()) {
                        val payload = JSONObject().put("version", version)
                            .put("stage", stage).put("detail", detail)
                        client.send(MessageTypes.OTA_PROGRESS, payload)
                    }
                },
            )
        }
        // 首次检查在服务启动后 30 秒（覆盖安装后尽快对齐版本），之后按 OTA_CHECK_INTERVAL_S 周期
        otaCheckFuture = otaScheduler.scheduleWithFixedDelay(
            { worker.execute { otaUpdater?.checkAndInstall() } },
            FIRST_OTA_CHECK_DELAY_S,
            OTA_CHECK_INTERVAL_S,
            TimeUnit.SECONDS,
        )
    }

    /**
     * ADB 桥接：root 开启 adbd TCP 后经 443 网关透传真实 adb 字节流。
     *
     * 守卫看的是"有没有实例"而不是"实例就绪没有"。只看 `ready` 的话，服务端在第一条
     * 还没连上时再发一条（adbd 拉起有重试窗口、WSS 握手也在其后）就会新建第二个实例：
     * 那一次 `stop adbd; start adbd` 会打断第一个刚建立的字节流，字段被覆盖后第一个实例
     * 永远没人 close（线程与 socket 泄漏），而它的关闭回调仍会执行 `adbBridge = null`
     * 与关 adbd——把新会话一起抹掉。现象是远程 adb 反复掉线，之后再也关不掉桥。
     */
    private fun openAdbBridge(envelope: Envelope) {
        val existing = adbBridge
        if (existing != null) {
            Log.i(TAG, "adb bridge already open (ready=${existing.ready})")
            return
        }
        val nonce = envelope.payload.optString("nonce")
        if (nonce.isEmpty()) {
            Log.w(TAG, "bridge.open missing nonce")
            return
        }
        val adb = RootShell.control.exec(
            "setprop service.adb.tcp.port ${AdbBridge.LOCAL_ADB_PORT}; " +
                "stop adbd; start adbd",
        )
        Log.i(TAG, "adbd tcp: exit=${adb.exitCode}")
        adbBridge = AdbBridge(
            BuildConfig.SERVER_DOMAIN,
            DeviceIdentity.deviceId(this),
            nonce,
            this,
        ) { reason ->
            Log.i(TAG, "adb bridge closed: $reason")
            adbBridge = null
            RootShell.control.exec(
                "setprop service.adb.tcp.port -1; stop adbd; start adbd",
            )
        }.also { it.start() }
    }

    private fun closeAdbBridge(reason: String) {
        adbBridge?.close(reason)
        adbBridge = null
    }

    /**
     * 启动喷头状态监视器。**回调只有这一份**：onStartCommand 与 applyConfig（配置变更
     * 重建监视器）都用它——那是同一种职责（"数据库变了就采集并上报"），写两份迟早分叉。
     */
    private fun startDoorMonitor() {
        monitor?.shutdown()
        monitor = DoorMonitor {
            worker.execute {
                // 喷头状态变化是客观事实，无论是否已连接都要采集入队。发件箱是本地
                // SQLite 持久化：未连接时 flush 的 send 会失败而自然留队，重连后
                // onConnected 里的 flush 按序补传——断网不丢数据。此前的实现把
                // "记录"错误地绑在 `trusted` 上，断网期间的变化在入队前就被丢弃，
                // 恢复后 seq 看似连续实则缺了整段（2026-11-03 TLS 故障暴露）。
                // 屏幕提示的本地占用事实不受影响：captureAndEnqueue 内部已通过
                // onDoorsRead 把读数投递给 notice/statusLine。
                sender.captureAndEnqueue(client, DoorSnapshotSender.SOURCE_DB_WATCH)
            }
        }.also { it.startWatching() }
    }

    /** 配置变更后重建监视器（防抖窗口等值可能已变），拉起逻辑与首次启动完全一致。 */
    private fun restartDoorMonitor() {
        monitor?.shutdown()
        startDoorMonitor()
    }

    private fun sendOutcome(outcome: CommandOutcome) {
        recentOutcomes.remember(outcome)
        deviceLog.result(outcome.commandId, outcome.status, outcome.reasonCode)
        val payload = JSONObject()
            .put("status", outcome.status)
            .put("reason_code", outcome.reasonCode)
            .put("terminal_message", outcome.terminalMessage)
        if (outcome.data != null) {
            payload.put("data", outcome.data)
        }
        // 与下发日志用同一个 command_id 对齐：两端日志能按 id 串成一条链路
        Log.i(
            TAG,
            "command result: id=${outcome.commandId} status=${outcome.status} reason=${outcome.reasonCode}",
        )
        client.send(MessageTypes.DEVICE_COMMAND_RESULT, payload, replyTo = outcome.commandId)
    }

    /**
     * 看门狗：按固定间隔拉活服务（对存活服务为幂等 onStartCommand）。收到 tick 后由
     * onStartCommand 重排下一次——逐次排精确闹钟而不是 [AlarmManager.setRepeating]：
     * 后者受系统闹钟批处理影响会有分钟级漂移且不可纠正，逐次排是官方推荐的连续告警
     * 形态，漂移不累积。
     *
     * 走 [AlarmManager.setExact]（ELAPSED_REALTIME_WAKEUP）：间隔的语义是"距上次
     * tick 过了这么久"，而不是"到了某个墙上时刻"。目标设备没有 NTP，系统时间会被
     * 运维改动、掉电重启后也会跳变——用 RTC 的话，时钟被往回调只会让 tick 静默
     * 推迟，自恢复链路整体延后，而日志里什么都看不出来。
     * `setExactAndAllowWhileIdle` 要 API 23，本设备是 19——`setExact` 是 19 上
     * 可用的精确形式。
     */
    private fun startWatchdog() {
        val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
        val pending = PendingIntent.getBroadcast(
            this,
            0,
            Intent(this, WatchdogReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val intervalMs = WATCHDOG_INTERVAL_S * 1000
        alarm.setExact(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            Uptime.nowMs() + intervalMs,
            pending,
        )
    }

    override fun onDestroy() {
        deviceLog.lifecycle("service.destroyed", "")
        // 先停掉一切会继续调度工作的东西，再拆连接：
        // 只断连接不关线程池，会让定时任务与服务线程在 Service 销毁后继续存活
        otaCheckFuture?.cancel(false)
        logShipFuture?.cancel(false)
        otaScheduler.shutdownNow()
        mainHandler.removeCallbacksAndMessages(null)
        worker.shutdownNow()
        maintenanceExecutor.shutdownNow()
        terminatingExecutor.shutdownNow()
        if (::scheduler.isInitialized) scheduler.shutdown()
        if (::overlay.isInitialized) overlay.destroy()
        if (::notice.isInitialized) notice.destroy()
        if (::statusLine.isInitialized) statusLine.destroy()
        if (::voicePlayer.isInitialized) voicePlayer.shutdown()
        closeAdbBridge("service destroyed")
        client.disconnect()
        monitor?.shutdown()
        RootShell.destroy()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "AgentService"

        private const val WATCHDOG_INTERVAL_S = 60L
        private const val COMMAND_DEDUP_MAX_COUNT = 1_024
        private const val DEVICE_EXEC_MAX_OUTPUT_BYTES = 32_768

        /** 首次 OTA 检查的延迟：覆盖安装后尽快对齐版本，之后按 OTA_CHECK_INTERVAL_S 周期。 */
        private const val OTA_CHECK_INTERVAL_S = 3_600L
        private const val FIRST_OTA_CHECK_DELAY_S = 30L

        /** 日志上送检查周期。 */
        private const val LOG_SHIP_TICK_S = 60L

        /** 非关键变化的普通上送间隔；关键事件不受它限制。 */
        private const val LOG_SHIP_PERIOD_MS = 5 * 60_000L

        /** 健康记录间隔：保证上送内容即使没有业务事件也在缓慢更新。 */
        private const val LOG_HEALTH_SNAPSHOT_MS = 30 * 60_000L
    }
}
