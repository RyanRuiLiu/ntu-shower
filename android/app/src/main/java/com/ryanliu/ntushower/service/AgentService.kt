package com.ryanliu.ntushower.service

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.net.HttpClientFactory
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.CommandFactory
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.automation.CommandResultCache
import com.ryanliu.ntushower.features.automation.CommandScheduler
import com.ryanliu.ntushower.features.automation.CoverOverlay
import com.ryanliu.ntushower.features.doors.DoorMonitor
import com.ryanliu.ntushower.features.doors.DoorSnapshotSender
import com.ryanliu.ntushower.features.remote.AdbBridge
import com.ryanliu.ntushower.features.selfmanage.A11yGuard
import com.ryanliu.ntushower.features.selfmanage.OtaUpdater
import com.ryanliu.ntushower.receiver.WatchdogReceiver
import com.ryanliu.ntushower.transport.Envelope
import com.ryanliu.ntushower.transport.GatewayClient
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * 设备主服务：无界面、后台常驻（START_STICKY）。
 *
 * 生命周期：开机拉起（本设备 = 无障碍重绑锚点；标准 ROM = BootReceiver）/
 * 覆盖安装自启（MY_PACKAGE_REPLACED）/ 看门狗定时拉活 / 崩溃自愈（ShowerApp）。
 * 多路径并发拉起经 onStartCommand 一次性初始化守卫收敛，无冲突。
 * 职责：网关长连接 + doors 采集 + 命令调度；后续功能在 features/ 中扩展并在此注册。
 */
class AgentService : Service() {

    private val worker = Executors.newSingleThreadExecutor()
    private val otaScheduler = Executors.newSingleThreadScheduledExecutor()

    /**
     * 维护命令执行器。
     *
     * 独立于 [worker]：`device.exec` 会阻塞到命令结束（上限 25 秒），塞进 [worker]
     * 会连带把门状态采集与上报停掉同样久；塞进主线程更糟——心跳、看门狗广播、
     * 无障碍事件都在那儿排队（见 [dispatchCommand]）。
     */
    private val maintenanceExecutor = Executors.newSingleThreadExecutor()
    private var otaCheckFuture: java.util.concurrent.ScheduledFuture<*>? = null
    private var otaUpdater: OtaUpdater? = null
    private lateinit var client: GatewayClient
    private lateinit var sender: DoorSnapshotSender
    private lateinit var overlay: CoverOverlay
    private lateinit var scheduler: CommandScheduler
    private var monitor: DoorMonitor? = null

    /** 已处理命令的结果缓存（幂等接收者，见 CommandResultCache）。 */
    private val recentOutcomes = CommandResultCache(MAX_REMEMBERED_COMMANDS)
    private var adbBridge: AdbBridge? = null

    @Volatile private var trusted = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 每次拉起都核对无障碍链路：看门狗周期（watchdog_interval_s，默认 60 秒）就是
        // 巡检周期。总开关被系统置 0 时本服务会失去无障碍这个自举锚点，看门狗的拉起
        // 是它仅剩的恢复机会——只在首次初始化时核对，这种情况下一次都不会执行。
        worker.execute { A11yGuard.ensureAvailable() }

        // 首次配置到达之前也要有个上限兜着：默认值就是登记表里的默认值
        RootShell.command.maxOutputBytes = RuntimeConfig.deviceExecMaxOutputBytes.toInt()

        if (!::client.isInitialized) {
            sender = DoorSnapshotSender(this)
            overlay = CoverOverlay(this)
            scheduler = CommandScheduler(overlay, onOutcome = { outcome -> sendOutcome(outcome) })
            client = GatewayClient(this, callback)
            client.connect()
            monitor = DoorMonitor {
                if (trusted) {
                    worker.execute {
                        sender.captureAndEnqueue(client)
                        sender.flush(client)
                    }
                }
            }.also { it.startWatching() }
            startWatchdog()
            scheduleOtaCheck()
            Log.i(TAG, "service started")
        }
        return START_STICKY
    }

    private val callback = object : GatewayClient.Callback {
        override fun onConnected(trusted: Boolean, config: JSONObject, upgrade: JSONObject?) {
            this@AgentService.trusted = trusted
            Log.i(TAG, "gateway connected, trusted=$trusted")
            if (trusted) {
                worker.execute {
                    // 服务端的门现态可能已经被清空重建（开发期"删了重建"是常规操作），
                    // 而设备侧状态可能一直没变。先忘掉指纹，这次采集才会无条件入队，
                    // 把那批门重新推一遍；只补发积压是不够的，那时发件箱多半是空的。
                    sender.forgetLastState()
                    sender.captureAndEnqueue(client)
                    // 断网期间积压的快照与门状态是否变化无关：重连时必须直接 flush，
                    // 否则门状态未变时积压数据永远不会重发。
                    sender.flush(client)
                }
            }
            if (upgrade != null) {
                Log.i(TAG, "upgrade available: ${upgrade.optString("version")}")
                worker.execute { otaUpdater?.checkAndInstall() }
            }
        }

        override fun onDisconnected(reason: String) {
            trusted = false
            Log.w(TAG, "gateway disconnected: $reason")
        }

        override fun onMessage(envelope: Envelope) {
            when (envelope.type) {
                "door.snapshot_ack" -> worker.execute {
                    sender.onAcked(envelope.payload.optString("snapshot_id"), client)
                }

                "session.command" -> dispatchCommand(envelope)

                "config.update" -> applyConfig(envelope)

                // 调试桥也要离开主线程：openAdbBridge 里的 setprop / stop adbd / start adbd
                // 是同步 shell 调用，closeAdbBridge 的关闭回调里还藏着一条——两条都会把
                // 主线程压住，连带排队心跳与无障碍事件。它们同属维护类，走同一个执行器，
                // 也就与 device.restart/uninstall 天然互斥（调试桥开着时重启设备本就危险）。
                "bridge.open" -> maintenanceExecutor.execute {
                    // 这两条不产生命令结果（服务端发出即返回，不等答复），但崩了必须留痕：
                    // 静默失败会让服务端一直等一个永远连不上来的设备，而日志里什么都没有
                    try {
                        openAdbBridge(envelope)
                    } catch (e: Throwable) {
                        Log.e(TAG, "bridge.open 失败：${e.message}", e)
                    }
                }

                "bridge.close" -> maintenanceExecutor.execute {
                    try {
                        closeAdbBridge("server requested")
                    } catch (e: Throwable) {
                        Log.e(TAG, "bridge.close 失败：${e.message}", e)
                    }
                }

                "ota.upgrade_available" -> {
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
            Log.i(TAG, "duplicate command, resending result: $commandId")
            sendOutcome(cached)
            return
        }
        val payload = envelope.payload
        val action = payload.optString("action", "")
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
        val checkpoint = System.nanoTime() / 1_000_000
        val timeoutMs = payload.optLong("timeout_ms", 0L)
        CommandFactory.create(action, params, commandId)?.let {
            scheduler.enqueue(it, checkpoint + timeoutMs)
            return
        }
        if (DeviceMaintenance.handles(action)) {
            maintenanceExecutor.execute { DeviceMaintenance.handle(action, params, commandId, ::sendOutcome) }
            return
        }
        sendOutcome(
            CommandOutcome(commandId, OutcomeStatus.FAILED, ReasonCodes.PROTO_UNKNOWN_ACTION, "设备不认识这个操作：$action"),
        )
    }

    private fun applyConfig(envelope: Envelope) {
        val config = envelope.payload.optJSONObject("config") ?: JSONObject()
        val revision = envelope.payload.optInt("revision", 0)
        // 配置统一入口：全部键回写 RuntimeConfig，随后刷新依赖配置的组件
        RuntimeConfig.apply(config)
        // 输出上限要在命令下发之前同步到通道上，之后设就晚了：
        // 读超了的部分已经在内存里
        RootShell.command.maxOutputBytes = RuntimeConfig.deviceExecMaxOutputBytes.toInt()
        client.refreshHeartbeat()
        scheduleOtaCheck()
        // 看门狗间隔变更：重建监视器与看门狗
        restartWatchdogAlarm()
        monitor?.stopWatching()
        monitor = DoorMonitor {
            if (trusted) {
                worker.execute {
                    sender.captureAndEnqueue(client)
                    sender.flush(client)
                }
            }
        }.also { it.startWatching() }
        client.send("config.ack", JSONObject().put("revision", revision), replyTo = envelope.id)
        Log.i(TAG, "config applied revision=$revision heartbeat=${RuntimeConfig.heartbeatIntervalS}s")
    }

    /** 定时检查 OTA：间隔来自服务端配置，变更后重新调度（复用同一 updater 与 HTTP 客户端）。 */
    private fun scheduleOtaCheck() {
        otaCheckFuture?.cancel(false)
        if (otaUpdater == null) {
            otaUpdater = OtaUpdater(
                this,
                HttpClientFactory.create(this, readTimeoutSeconds = 60),
                onProgress = { version, stage, detail ->
                    Log.i(TAG, "ota $version $stage $detail")
                    if (client.isReady()) {
                        val payload = JSONObject().put("version", version)
                            .put("stage", stage).put("detail", detail)
                        client.send("ota.progress", payload)
                    }
                },
            )
        }
        // 首次检查在服务启动后 30 秒（覆盖安装后尽快对齐版本），之后按配置间隔
        otaCheckFuture = otaScheduler.scheduleWithFixedDelay(
            { worker.execute { otaUpdater?.checkAndInstall() } },
            FIRST_OTA_CHECK_DELAY_S,
            RuntimeConfig.otaCheckIntervalS.toLong(),
            java.util.concurrent.TimeUnit.SECONDS,
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

    private fun sendOutcome(outcome: CommandOutcome) {
        recentOutcomes.remember(outcome)
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
        client.send("session.command_result", payload, replyTo = outcome.commandId)
    }

    private fun restartWatchdogAlarm() {
        val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
        alarm.cancel(PendingIntent.getBroadcast(this, 0, Intent(this, WatchdogReceiver::class.java), 0))
        startWatchdog()
    }

    /**
     * 看门狗：按配置间隔拉活服务（对存活服务为幂等 onStartCommand）。
     *
     * 走 [AlarmManager.ELAPSED_REALTIME_WAKEUP]：间隔的语义是"距上次 tick 过了这么久"，
     * 而不是"到了某个墙上时刻"。目标设备没有 NTP，系统时间会被运维改动、掉电重启后
     * 也会跳变——用 RTC 的话，时钟被往回调只会让 tick 静默推迟，自恢复链路整体延后，
     * 而日志里什么都看不出来。
     */
    private fun startWatchdog() {
        val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
        val pending = PendingIntent.getBroadcast(
            this,
            0,
            Intent(this, WatchdogReceiver::class.java),
            0,
        )
        val intervalMs = RuntimeConfig.watchdogIntervalS * 1000
        alarm.setRepeating(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + intervalMs,
            intervalMs,
            pending,
        )
    }

    override fun onDestroy() {
        // 先停掉一切会继续调度工作的东西，再拆连接：
        // 只断连接不关线程池，会让定时任务与服务线程在 Service 销毁后继续存活
        otaCheckFuture?.cancel(false)
        otaScheduler.shutdownNow()
        worker.shutdownNow()
        maintenanceExecutor.shutdownNow()
        closeAdbBridge("service destroyed")
        client.disconnect()
        monitor?.stopWatching()
        RootShell.destroy()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "AgentService"

        /** 判定命令过期时的容差，见 [isExpired]。 */
        private const val DEADLINE_TOLERANCE_MS = 2_000L

        /** 首次 OTA 检查的延迟：覆盖安装后尽快对齐版本，之后按配置间隔。 */
        private const val FIRST_OTA_CHECK_DELAY_S = 30L

        /** 幂等去重窗口：只记最近若干条命令结果，不随运行时长增长。 */
        private const val MAX_REMEMBERED_COMMANDS = 64
    }
}
