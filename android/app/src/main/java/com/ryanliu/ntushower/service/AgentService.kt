package com.ryanliu.ntushower.service

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.net.HttpClientFactory
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.automation.CommandScheduler
import com.ryanliu.ntushower.features.automation.CommandScheduler.Command
import com.ryanliu.ntushower.features.automation.CoverOverlay
import com.ryanliu.ntushower.features.doors.DoorMonitor
import com.ryanliu.ntushower.features.doors.DoorSnapshotSender
import com.ryanliu.ntushower.features.remote.AdbBridge
import com.ryanliu.ntushower.features.selfmanage.A11yEnabler
import com.ryanliu.ntushower.features.selfmanage.OtaUpdater
import com.ryanliu.ntushower.receiver.WatchdogReceiver
import com.ryanliu.ntushower.transport.Envelope
import com.ryanliu.ntushower.transport.GatewayClient
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
    private var otaCheckFuture: java.util.concurrent.ScheduledFuture<*>? = null
    private var otaUpdater: OtaUpdater? = null
    private lateinit var client: GatewayClient
    private lateinit var sender: DoorSnapshotSender
    private lateinit var overlay: CoverOverlay
    private lateinit var scheduler: CommandScheduler
    private var monitor: DoorMonitor? = null
    private var adbBridge: AdbBridge? = null

    @Volatile private var trusted = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!::client.isInitialized) {
            worker.execute { A11yEnabler.ensureEnabled() } // 幂等：root 启用无障碍（append 语义）
            sender = DoorSnapshotSender(this)
            overlay = CoverOverlay(this)
            scheduler = CommandScheduler(overlay) { outcome -> sendOutcome(outcome) }
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

                "bridge.open" -> openAdbBridge(envelope)

                "bridge.close" -> closeAdbBridge("server requested")

                "ota.upgrade_available" -> {
                    // 实时推送：立即触发一次检查（自动化期间 OtaUpdater 自行推迟）
                    worker.execute { otaUpdater?.checkAndInstall() }
                }
            }
        }
    }

    private fun dispatchCommand(envelope: Envelope) {
        val commandId = envelope.id
        val payload = envelope.payload
        val action = payload.optString("action", "")
        val params = payload.optJSONObject("params") ?: JSONObject()
        Log.i(TAG, "command: $action ($commandId)")
        when (action) {
            "session.assign" -> scheduler.enqueue(
                Command.Assign(
                    id = commandId,
                    doorNo = params.optInt("door_no"),
                    certNo = params.optString("cert_no"),
                    password = params.optString("password"),
                ),
            )

            "session.settle" -> scheduler.enqueue(
                Command.Settle(
                    id = commandId,
                    certNo = params.optString("cert_no"),
                    password = params.optString("password"),
                ),
            )

            "photo.capture" -> scheduler.enqueue(Command.Photo(id = commandId))

            "screen.capture" -> scheduler.enqueue(Command.Screen(id = commandId))

            "app.restart" -> {
                val appId = BuildConfig.APPLICATION_ID
                sendOutcome(CommandOutcome(commandId, "ok", "", ""))
                // 分离 root 进程延迟执行：命令回报先送达，随后本进程被强制停止并拉起
                RootShell.exec(
                    "(sleep 2; am force-stop $appId; sleep 2; " +
                        "am startservice -n $appId/.service.AgentService) >/dev/null 2>&1 &",
                )
            }

            "debug.exec" -> {
                val command = params.optString("command", "")
                val result = RootShell.exec(command)
                sendOutcome(
                    CommandOutcome(
                        commandId,
                        if (result.success) "ok" else "failed",
                        "",
                        terminalMessage = "",
                        data = JSONObject().put("output", result.output),
                    ),
                )
            }

            "app.uninstall" -> {
                val appId = BuildConfig.APPLICATION_ID
                sendOutcome(CommandOutcome(commandId, "ok", "", ""))
                RootShell.exec(
                    "(sleep 2; pm uninstall $appId) >/dev/null 2>&1 &",
                )
            }

            else -> sendOutcome(
                CommandOutcome(commandId, "failed", "proto.unknown_action", "unknown action: $action"),
            )
        }
    }

    private fun applyConfig(envelope: Envelope) {
        val config = envelope.payload.optJSONObject("config") ?: JSONObject()
        val revision = envelope.payload.optInt("revision", 0)
        // 配置统一入口：全部键回写 RuntimeConfig，随后刷新依赖配置的组件
        RuntimeConfig.apply(config)
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

    /** ADB 桥接：root 开启 adbd TCP 后经 443 网关透传真实 adb 字节流。 */
    private fun openAdbBridge(envelope: Envelope) {
        if (adbBridge?.ready == true) {
            Log.i(TAG, "adb bridge already open")
            return
        }
        val nonce = envelope.payload.optString("nonce")
        if (nonce.isEmpty()) {
            Log.w(TAG, "bridge.open missing nonce")
            return
        }
        val adb = RootShell.exec(
            "setprop service.adb.tcp.port ${AdbBridge.LOCAL_ADB_PORT}; " +
                "stop adbd; start adbd",
        )
        Log.i(TAG, "adbd tcp: success=${adb.success}")
        adbBridge = AdbBridge(
            BuildConfig.SERVER_DOMAIN,
            DeviceIdentity.deviceId(this),
            nonce,
            this,
        ) { reason ->
            Log.i(TAG, "adb bridge closed: $reason")
            adbBridge = null
            RootShell.exec(
                "setprop service.adb.tcp.port -1; stop adbd; start adbd",
            )
        }.also { it.start() }
    }

    private fun closeAdbBridge(reason: String) {
        adbBridge?.close(reason)
        adbBridge = null
    }

    private fun sendOutcome(outcome: CommandOutcome) {
        val payload = JSONObject()
            .put("status", outcome.status)
            .put("reason_code", outcome.reasonCode)
            .put("terminal_message", outcome.terminalMessage)
        if (outcome.data != null) {
            payload.put("data", outcome.data)
        }
        client.send("session.command_result", payload, replyTo = outcome.commandId)
    }

    private fun restartWatchdogAlarm() {
        val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
        alarm.cancel(PendingIntent.getBroadcast(this, 0, Intent(this, WatchdogReceiver::class.java), 0))
        startWatchdog()
    }

    /** 看门狗：按配置间隔拉活服务（对存活服务为幂等 onStartCommand）。 */
    private fun startWatchdog() {
        val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
        val pending = PendingIntent.getBroadcast(
            this,
            0,
            Intent(this, WatchdogReceiver::class.java),
            0,
        )
        alarm.setRepeating(
            AlarmManager.RTC_WAKEUP,
            System.currentTimeMillis() + RuntimeConfig.watchdogIntervalS * 1000,
            RuntimeConfig.watchdogIntervalS * 1000,
            pending,
        )
    }

    override fun onDestroy() {
        client.disconnect()
        monitor?.stopWatching()
        RootShell.destroy()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "AgentService"
        private const val FIRST_OTA_CHECK_DELAY_S = 30L
    }
}
