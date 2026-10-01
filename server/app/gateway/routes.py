"""设备 WS 网关平面：/gateway/device。

帧处理规则：
- 第一帧必须是 link.hello，否则报错断开；
- link.ping → link.pong（心跳回路）；
- 未知 type → link.error(proto.unknown_type)，不断开（协议扩展点）；
- 未信任设备保持连接但不进入任何业务。
"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..core import events, outcomes, protocol, reason_codes
from ..core.clock import now_rfc3339, rfc3339_ago
from ..core.device_clock import load_skew, ping_payload, update_skew
from ..core.protocol import JsonObject, as_int, list_of, payload_of, text_of
from ..modules.config import store as config_store
from ..modules.devices import repository as devices_repository
from ..modules.devices import service as devices_service
from ..modules.doors import service as doors_service
from ..modules.ota import repository as ota_repository
from ..modules.sessions import service as sessions_service
from .manager import manager

logger = logging.getLogger("ntu-shower.gateway")

router = APIRouter()

#: 主动关闭连接时用的 WS 状态码。与设备端 `GatewayClient.CLOSE_GOING_AWAY` 同值。
CLOSE_GOING_AWAY = 1001


@router.websocket("/gateway/device")
async def device_gateway(ws: WebSocket) -> None:
    await ws.accept()
    device_id: str | None = None
    ping_task: asyncio.Task[None] | None = None
    #: 每次收到设备 pong 由 `_handle_frame` 置位；`_server_ping_loop` 用它判断链路是否还活着。
    pong_seen = asyncio.Event()
    trusted = False
    try:
        hello = protocol.parse(await ws.receive_text())
        if hello is None or hello.get("type") != "link.hello":
            logger.warning("rejected connection: first frame is not link.hello")
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
        # 开机时刻由**服务端**的反推得出（此刻减去设备报的运行秒数），不采信设备的墙上
        # 时钟——这台机器没有 NTP，它的绝对时间只能靠与我们的往返来校准，而运行时长是
        # 个单调量，不受那个偏差影响。
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
        # 必须早于 latest_for，否则本次 hello 仍会推送刚装过的版本——force 发布
        # 会因此形成「下发→下载→安装→重启→再下发」的无限重装循环（真机实测复现）。
        await run_in_threadpool(ota_repository.mark_installed_upto, device_id, version_code)
        manager.register(device_id, ws)
        logger.info(
            "device connected: id=%s trusted=%s version=%s os=%s capabilities=%d ip=%s",
            device_id,
            trusted,
            text_of(hello_payload, "app_version"),
            text_of(hello_payload, "os_version"),
            len(list_of(hello_payload, "capabilities")),
            ip,
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

        # 偏差从库里恢复，再做一次主动探测。
        #
        # 两件事缺一不可：设备上报的 openTime/inTime/closeTime 是它的墙上时钟（实测有
        # 一台快 59 分 47 秒），落库前全靠这个偏差校正。不载入的话，服务重启后偏差是
        # 0——那等于不校正；不主动探测的话，要等 `ping_interval_s`（默认 60 秒）才轮到
        # 第一轮 ping，那之前到达的快照同样没校正。
        load_skew(device_id, await run_in_threadpool(devices_repository.get_clock_skew, device_id))
        await ws.send_text(ping_payload())

        events.publish(events.DEVICE_UPDATE, await _device_snapshot(device_id))
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
        logger.warning("gateway session failed: id=%s error=%s", device_id or "<unknown>", exc)
        raise
    finally:
        if ping_task is not None:
            ping_task.cancel()
        if device_id:
            manager.unregister(device_id, ws)
            await run_in_threadpool(devices_service.set_device_offline, device_id)
            logger.info("device disconnected: id=%s", device_id)
            events.publish(events.DEVICE_UPDATE, await _device_snapshot(device_id))


async def _device_snapshot(device_id: str) -> JsonObject:
    """取设备的完整档案，作为 `device.update` 的 payload。

    **推送的是整条记录，不是一个"上线了/下线了"的状态词。** 设备重连时变的不只是
    `online`——版本号（OTA 升级后）、来源 IP（换网络后）、开机时刻（重启后）都在服务端
    这一侧刚被更新过。只推状态词的话，前端收到也没有别的字段可更新，那些变化就永远到不了
    界面。

    取不到时给一个只含 id 与"不在线"的最小对象：设备刚被删掉的窗口里确实可能查不到，
    而推送不该因此断掉。
    """
    record = await run_in_threadpool(devices_service.get_device, device_id)
    if record is not None:
        return dict(record)
    return {"device_id": device_id, "online": False}


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
        # 先记"收到了它"再回 pong：顺序反过来时，收到的这一帧已经处理完而记录还没落，
        # 外面看到 pong 就以为那一列已经刷新了。
        await run_in_threadpool(devices_service.touch_seen, device_id)
        await ws.send_text(protocol.envelope("link.pong", {"sender_time": now_rfc3339()}, reply_to=frame_id))
    elif mtype == "link.pong":
        config = await run_in_threadpool(config_store.effective, device_id)
        threshold = as_int(config.get("clock_jump_threshold_s"), default=300)
        skew = update_skew(device_id, text_of(payload, "sender_time"), threshold)
        await run_in_threadpool(devices_repository.set_clock_skew, device_id, skew)
        await run_in_threadpool(devices_service.touch_seen, device_id)
        pong_seen.set()
    elif mtype == "config.ack":
        sessions_service.resolve_config_ack(device_id, as_int(payload.get("revision")))
    elif mtype == "door.snapshot":
        if not trusted:
            return
        result = await run_in_threadpool(doors_service.process_snapshot, device_id, payload)
        if result["status"] == outcomes.OK.value:
            await ws.send_text(
                protocol.envelope(
                    "door.snapshot_ack",
                    {"snapshot_id": text_of(payload, "snapshot_id")},
                    reply_to=frame_id,
                )
            )
        else:
            error = result["error"]
            await ws.send_text(protocol.error(str(error["code"]), str(error["message"]), reply_to=frame_id))
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
    """按设备有效配置的间隔向设备发服务器 ping，并据此判断链路是否还活着。

    发 ping 原本只为驱动时钟偏差测量，但它同时是这条连接**唯一**的存活探针：TCP 半开时
    （设备进了隧道、信号丢失、中间设备静默丢包）`receive_text` 不会抛错，服务端会一直
    以为设备在线——界面上的"在线"也就是假的，要等到 TCP 自己超时，那可能是几十分钟后。

    所以每轮发 ping 前清掉标记，最多等到下一个 ping 该发的时刻。没等到 pong 就主动断开，
    让 finally 里的 `device.update` 正常推出去。等待时长取 ping 间隔本身：设备处理 pong
    是即时的（它的读循环独立于命令通道），一个完整间隔没有回应不是慢，是断了。

    连接关闭即正常退出；其他异常记录后退出——静默吞掉会让时钟校准无声失效。
    """
    while True:
        config = await run_in_threadpool(config_store.effective, device_id)
        interval = as_int(config.get("ping_interval_s"), default=60)
        # 先等满一个周期再发首个 ping：刚握手成功的连接不需要立刻验证存活
        await asyncio.sleep(interval)
        pong_seen.clear()
        try:
            await ws.send_text(ping_payload())
        except (WebSocketDisconnect, RuntimeError) as exc:
            logger.debug("server ping loop ended for %s: %s", device_id, exc)
            return
        try:
            await asyncio.wait_for(pong_seen.wait(), timeout=interval)
        except asyncio.TimeoutError:
            logger.warning("device %s gave no pong within %ss, closing the link", device_id, interval)
            with contextlib.suppress(RuntimeError):
                await ws.close(code=CLOSE_GOING_AWAY)
            return
