"""设备 WS 网关平面：/gateway/device。

帧处理规则：
- 第一帧必须是 link.hello，否则报错断开；
- link.ping → link.pong（心跳回路）；
- 未知 type → link.error(proto.unknown_type)，不断开（协议扩展点）；
- 未信任设备保持连接但不进入任何业务。
"""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..core import audit, protocol, reason_codes
from ..core.clock import now_rfc3339, rfc3339_ago
from ..core.connections import manager
from ..core.logging import log_event
from ..core.protocol import JsonObject, as_int, list_of, payload_of, text_of
from ..modules.config import store as config_store
from ..modules.devices import service as devices_service
from ..modules.devices.schemas import LINK_OFFLINE, LINK_ONLINE
from ..modules.doors import service as doors_service
from ..modules.ota import repository as ota_repository
from ..modules.sessions import service as sessions_service

router = APIRouter()

#: 主动关闭连接时用的 WS 状态码。与设备端 `GatewayClient.CLOSE_GOING_AWAY` 同值。
CLOSE_GOING_AWAY = 1001


@router.websocket("/gateway/device")
async def device_gateway(ws: WebSocket) -> None:
    await ws.accept()
    device_id: str | None = None
    ip = ""
    ping_task: asyncio.Task[None] | None = None
    #: 每次收到设备 pong 由 `_handle_frame` 置位；`_server_ping_loop` 用它判断链路是否还活着。
    pong_seen = asyncio.Event()
    trusted = False
    try:
        hello = protocol.parse(await ws.receive_text())
        if hello is None or hello.get("type") != "link.hello":
            log_event("WARNING", "gateway.hello-required", "首帧必须是 link.hello，连接被拒")
            await ws.send_text(protocol.error(reason_codes.HELLO_REQUIRED.code, "首帧必须是 link.hello"))
            await ws.close()
            return

        hello_payload = payload_of(hello)
        device_id = text_of(hello_payload, "device_id")
        version_code = as_int(hello_payload.get("version_code"))
        row = await run_in_threadpool(devices_service.get_device, device_id)
        trusted = bool(row and row["trusted"])
        # 经 nginx 代理而来：真实设备 IP 在 X-Real-IP 头里
        ip = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
        # 开机时刻由服务端反推（此刻减设备上报的运行秒数）：运行时长是单调量，
        # 不受设备墙上时钟偏差影响
        booted_at = rfc3339_ago(as_int(hello_payload.get("uptime_s")))
        await run_in_threadpool(
            devices_service.register_device,
            device_id,
            text_of(hello_payload, "app_version"),
            version_code,
            text_of(hello_payload, "os_version"),
            list_of(hello_payload, "capabilities"),
            ip,
            booted_at,
        )
        # 安装确认：设备以某 version_code 上线，即证明所有 ≤ 该版本的发布已装在它身上。
        # 必须早于 latest_for，否则本次 hello 仍会推送刚装过的版本，force 发布会形成
        # 「下发→下载→安装→重启→再下发」的无限重装循环。
        await run_in_threadpool(ota_repository.mark_installed_upto, device_id, version_code)
        manager.register(device_id, ws)
        # 载入上次记下的钟偏差。从上线到首次 pong 之间有一小段窗口，不载入的话那段时间
        # 读到的是 0（等于不平移）——而"我们刚接手、那边已经在洗"恰恰最常落在窗口里。
        await run_in_threadpool(devices_service.load_clock_skew, device_id)
        await run_in_threadpool(devices_service.record_link_event, device_id, LINK_ONLINE)
        await run_in_threadpool(audit.record_gateway, device_id, "connect", ip)
        log_event(
            "INFO",
            "gateway.device-connected",
            "设备接入",
            device_id=device_id,
            trusted=trusted,
            version=text_of(hello_payload, "app_version"),
            os=text_of(hello_payload, "os_version"),
            capabilities=len(list_of(hello_payload, "capabilities")),
            ip=ip,
        )
        await ws.send_text(
            protocol.envelope(
                "link.hello_ack",
                {
                    "trusted": trusted,
                    "server_time": now_rfc3339(),
                    "config": await run_in_threadpool(config_store.effective, device_id),
                    "upgrade": await run_in_threadpool(ota_repository.latest_for, device_id, version_code),
                },
                reply_to=text_of(hello, "id"),
            )
        )

        # 立刻主动探测一轮，不等 ping_interval_s：设备刚连上时手上还没有时间锚点，而
        # 它采集的每一帧都要带采集时刻——越早把服务端时刻交给它，最初那几帧时刻越准。
        await ws.send_text(protocol.envelope("link.ping", {"sender_time": now_rfc3339()}))

        devices_service.publish_device_update(device_id)
        ping_task = asyncio.create_task(_server_ping_loop(ws, device_id, pong_seen))

        while True:
            msg = protocol.parse(await ws.receive_text())
            if msg is None:
                await ws.send_text(protocol.error(reason_codes.BAD_JSON.code, "收到的帧不是合法 JSON 对象"))
                continue
            # 未知类型只回错不断开：这是协议的前向扩展点
            await _handle_frame(ws, msg, device_id, trusted, pong_seen)
    except WebSocketDisconnect:
        # 设备离线是常态（移动网络/重启），不作为异常处理
        pass
    except Exception as exc:
        # 其他异常必须留痕：静默会让"设备莫名掉线"无从追查
        log_event(
            "WARNING",
            "gateway.session-failed",
            "网关会话异常",
            device_id=device_id or "<unknown>",
            error=str(exc),
        )
        raise
    finally:
        if ping_task is not None:
            ping_task.cancel()
        if device_id:
            # 只有"我这条连接确实是当前那条"才算一次真离线。重连是新连接顶替旧连接，
            # 被顶替的那条随后走这里收尾——那一刻设备仍在线，不该记 offline。实测这台
            # 设备一天重连两百次上下，假 offline 会占掉绝大多数。
            if manager.unregister(device_id, ws):
                await run_in_threadpool(devices_service.record_link_event, device_id, LINK_OFFLINE)
            await run_in_threadpool(devices_service.set_device_offline, device_id)
            await run_in_threadpool(audit.record_gateway, device_id, "disconnect", ip)
            log_event("INFO", "gateway.device-disconnected", "设备断开", device_id=device_id)
            devices_service.publish_device_update(device_id)


async def _handle_frame(
    ws: WebSocket,
    msg: JsonObject,
    device_id: str,
    trusted: bool,
    pong_seen: asyncio.Event,
) -> None:
    """分发一帧入站报文。未信任设备只放行链路层消息，业务帧静默忽略。"""
    mtype = text_of(msg, "type")
    frame_id = text_of(msg, "id")
    payload = payload_of(msg)

    if mtype == "link.ping":
        # 先记录再回 pong：反过来时 pong 已发出而记录未落，外部会看到时序颠倒。
        # 服务端回的这一份 pong 载荷为空——反方向那份才带 device_time，那是给服务端
        # 算钟偏差用的（见下一条分支）。
        await run_in_threadpool(devices_service.touch_seen, device_id)
        await ws.send_text(protocol.envelope("link.pong", {}, reply_to=frame_id))
    elif mtype == "link.pong":
        # 两件事：判活，以及用设备带回来的墙上时刻更新钟偏差。那个偏差把快照里的
        # open_time / in_time 平移到服务端时间轴——那两个字段是"这次会话从什么时候
        # 开始"的唯一线索（见 core/device_clock）。
        #
        # `received_at` 在这一层取一次就往下传：函数执行已经晚于报文到达，让下游再取
        # 一次只会白添一个调用栈的误差。
        await run_in_threadpool(devices_service.touch_seen, device_id)
        await run_in_threadpool(
            devices_service.update_clock_skew,
            device_id,
            payload.get("device_time"),
            now_rfc3339(),
        )
        pong_seen.set()
    elif mtype == "config.ack":
        sessions_service.resolve_config_ack(device_id, as_int(payload.get("revision")))
    elif mtype == "door.snapshot":
        if not trusted:
            return
        # 帧自己带着"采集那一刻的设备钟读数"，与 captured_at 同刻——比 pong 那条通路准得多
        # （pong 的两个读数隔着一个网络往返，是秒级）。所以偏差在**帧粒度**上刷新，会话起点
        # 才有可能精确；pong 只负责在空闲时段把已经稳住的偏差维持住。
        #
        # 与 link.pong 分支对称：两处都只是把"设备报的时刻"和"服务端那一刻"交给同一个
        # update_clock_skew。缺任一个就跳过——设备还没锚上时 captured_at 本来就没有。
        device_clock_text = payload.get("device_clock")
        captured_at = payload.get("captured_at")
        if isinstance(device_clock_text, str) and isinstance(captured_at, str):
            await run_in_threadpool(
                devices_service.update_clock_skew,
                device_id,
                device_clock_text,
                captured_at,
            )
        # 回执与派生结果解耦：原始帧是 process_snapshot 落的第一样东西，那之后无论派生
        # 出什么岔子，"这一帧已经收下"都成立。
        #
        # 所以派生异常**在这里止住**，不能让它冒泡到会话层的 `raise`——那会把一个数据问题
        # 升级成连接问题：WS 一断，设备立刻重发同一帧，而派生是确定性的，它在同一处再失败、
        # 再断，循环下去。断连的代价还不止这一帧：`finally` 会记 offline、推 device.update
        # （网页上看到设备闪断），其他门正在推进的会话也被打断。
        #
        # 数据不会因此丢：派生失败可以事后 `tools/rebuild_derived` 从原始层重算，回执让
        # 设备删掉发件箱里那一行才是对的。
        try:
            await run_in_threadpool(doors_service.process_snapshot, device_id, payload)
        except Exception as exc:  # noqa: BLE001 - 派生可能以任何方式失败，而这一帧已经收下了
            log_event(
                "ERROR",
                "doors.snapshot-derive-failed",
                "派生失败，原始帧已落库",
                device_id=device_id,
                error=str(exc),
            )
        await ws.send_text(
            protocol.envelope(
                "door.snapshot_ack",
                {"snapshot_id": text_of(payload, "snapshot_id")},
                reply_to=frame_id,
            )
        )
    elif mtype == "session.command_result":
        if not trusted:
            return
        if not sessions_service.resolve(text_of(msg, "reply_to"), payload):
            # 回执找不到对应的在途命令（多半是命令已超时收尾）：回错误帧保持
            # 协议完整性反馈，设备端只记日志，不产生副作用。
            await ws.send_text(
                protocol.error(reason_codes.UNKNOWN_COMMAND_ID.code, "没有与该结果对应的待办命令", reply_to=frame_id)
            )
    else:
        await ws.send_text(protocol.error(reason_codes.UNKNOWN_TYPE.code, f"不认识的消息类型：{mtype}"))


async def _server_ping_loop(ws: WebSocket, device_id: str, pong_seen: asyncio.Event) -> None:
    """按设备有效配置的间隔向设备发服务器 ping：判活，并给设备刷新时间锚点。

    两个职责共用同一条报文：ping 里带着服务端此刻的时刻，设备收到就更新自己的时间
    锚点（core/clock/ServerClock），它采集的每一帧都带上那个时刻。

    判活靠它，是因为这是连接唯一的存活探针：TCP 半开时 receive_text 不会抛错，没有它
    服务端会长期以为设备在线。每轮发 ping 前清掉标记，最多等一个 ping 周期；没等到
    pong 就主动断开，让 finally 里的 device.update 正常推出。等待时长取 ping 间隔
    本身：设备处理 pong 是即时的，一个完整周期没有回应不是慢，是断了。

    连接关闭即正常退出；其他异常记录后退出，静默吞掉会让存活判定无声失效。
    """
    while True:
        config = await run_in_threadpool(config_store.effective, device_id)
        interval = as_int(config.get("ping_interval_s"), default=60)
        # 先等满一个周期再发首个 ping：刚握手成功的连接不需要立刻验证存活
        await asyncio.sleep(interval)
        pong_seen.clear()
        try:
            await ws.send_text(protocol.envelope("link.ping", {"sender_time": now_rfc3339()}))
        except (WebSocketDisconnect, RuntimeError) as exc:
            log_event(
                "DEBUG",
                "gateway.ping-loop-ended",
                "服务端 ping 循环结束（链路已断）",
                device_id=device_id,
                error=str(exc),
            )
            return
        try:
            await asyncio.wait_for(pong_seen.wait(), timeout=interval)
        except asyncio.TimeoutError:
            log_event(
                "WARNING",
                "gateway.pong-timeout",
                "设备一个周期内没有应答 pong，关闭链路",
                device_id=device_id,
                interval_s=int(interval),
            )
            with contextlib.suppress(RuntimeError):
                await ws.close(code=CLOSE_GOING_AWAY)
            return
