"""设备 WS 网关平面：/gateway/device。

帧处理规则：
- 第一帧必须是 link.hello，否则报错断开；
- 服务端周期发 link.ping，设备以 link.pong 应答（判活 + 时间锚点；设备主动发的
  ping 不是本协议的一部分，来了按未知类型回错）；
- 未知 type → link.error(proto.unknown_type)，不断开（协议扩展点）；
- 未信任设备保持连接但不进入任何业务。
"""

import asyncio
import contextlib
from collections.abc import Callable
from typing import cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import protocol, reason_codes
from app.core.clock import now_rfc3339, rfc3339_ago
from app.core.config import settings
from app.core.connections import manager
from app.core.logging import log_event
from app.core.message_types import (
    CONFIG_UPDATE_ACK,
    DEVICE_COMMAND_RESULT,
    DEVICE_LOG_UPLOAD,
    DEVICE_STATUS,
    DOOR_SNAPSHOT,
    DOOR_SNAPSHOT_ACK,
    LINK_HELLO,
    LINK_HELLO_ACK,
    LINK_PING,
    LINK_PONG,
    OTA_PROGRESS,
)
from app.core.protocol import JsonObject, as_int, list_of, payload_of, text_of
from app.core.tokens import extract_bearer
from app.db.session import Database
from app.modules.config import store as config_store
from app.modules.config.spec import parse_time
from app.modules.devices import actions as device_actions
from app.modules.devices import crud as devices_crud
from app.modules.devices import service as devices_service
from app.modules.devices.schemas import LINK_OFFLINE, LINK_ONLINE
from app.modules.doors import service as doors_service
from app.modules.ota import crud as ota_crud
from app.modules.power import service as power_service
from app.modules.sessions import scheduler as sessions_scheduler
from app.modules.voice import service as voice_service

router = APIRouter()


#: 设备已停用（RFC 6455 §7.4.2 的应用私用段 4000-4999，两端约定同一解释）。
#: 与 1008 的分工：1008 = 凭据无效 → 设备清凭据重走注册；4403 = 设备被除名 →
#: 注册救不回来，设备停止重连等人工。
WS_CLOSE_DEVICE_REVOKED = 4403
WS_CLOSE_GOING_AWAY = 1001


def _db_session(ws: WebSocket) -> Session:
    """WS 路径的会话来源：应用状态里的 Database，短生命周期。"""
    database = cast("Database", ws.app.state.database)
    return database.new_session()


async def _call[T](ws: WebSocket, fn: Callable[..., T], *args: object) -> T:
    """在线程池里执行一个收 `session` 首参的同步服务函数。

    WS 帧处理没有请求上下文，设备域服务已改为显式注入；这里是 WS 侧唯一的
    会话来源，等价于路由层的 `Depends(get_db)`。

    会话在**线程函数内部**开、内部关（铁律：会话在哪个线程里生，就在哪个线程里
    关）。此前会话在事件循环线程上开、交给线程池用、回到循环线程上关——调用方
    取消（门户关闭、客户端断连）时，`run_in_threadpool` 的取消不会停掉线程函数，
    with 块却在线程函数仍在执行 SQL 的情况下收线：同一连接被两条线程并发触碰，
    正是 background.py 记载的 SQLite 原生崩溃（测试并行时的 worker segfault）
    的残留通路。整个会话生命周期待在线程函数里后，与取消语义解耦。
    """

    def _run() -> T:
        with _db_session(ws) as session:
            return fn(session, *args)

    return await run_in_threadpool(_run)


@router.websocket("/gateway/device")
async def device_gateway(ws: WebSocket) -> None:
    """一条设备连接的一生：握手 → 验凭据 → 帧循环 → 收尾。

    凭据校验放在 accept 之后：握手前拒绝在 ASGI 下只能回 HTTP 403，客户端拿不到
    WebSocket 关闭码，设备端就无法把「凭据被拒」与普通断连区分开、也就不会触发
    自注册。accept 之后以 1008 关闭是 RFC 6455 给策略拒绝定的关闭码，客户端的
    `onClosed(1008)` 能收到，据此清凭据重走 claim 注册。
    """
    await ws.accept()
    allowed, token_device_id = await _verify_device_token(ws)
    if not allowed:
        return
    ping_task: asyncio.Task[None] | None = None
    device_id: str | None = None
    try:
        hello_state = await _accept_hello(ws, token_device_id)
        if hello_state is None:
            return
        device_id, trusted, _ip = hello_state

        # 立刻主动锚定一轮，不等周期锚定（PING_INTERVAL_S）：设备刚连上时手上还没有时间锚点，而
        # 它采集的每一帧都要带采集时刻——越早把服务端时刻交给它，最初那几帧时刻越准。
        await ws.send_text(protocol.envelope(LINK_PING, {"sender_time": now_rfc3339()}))

        with _db_session(ws) as session:
            devices_service.publish_device_update(session, device_id)
        ping_task = asyncio.create_task(_anchor_ping_loop(ws, device_id))

        while True:
            msg = protocol.parse(await ws.receive_text())
            if msg is None:
                await ws.send_text(protocol.error(reason_codes.BAD_JSON.code, "收到的帧不是合法 JSON 对象"))
                continue
            if msg.get("v") != protocol.PROTOCOL_VERSION:
                # 版本不符等于双方说的是两套协议：回错后断开（1002）。不静默丢帧——
                # 那会让新协议设备表现为"连着但什么都不动"，比明确拒绝更难排查。
                log_event(
                    "WARNING",
                    "gateway.version-mismatch",
                    "帧协议版本不符，连接关闭",
                    device_id=device_id,
                    server_version=protocol.PROTOCOL_VERSION,
                    frame_version=msg.get("v"),
                )
                await ws.send_text(
                    protocol.error(
                        reason_codes.VERSION_MISMATCH.code,
                        f"服务端协议版本为 {protocol.PROTOCOL_VERSION}，收到的帧携带 {msg.get('v')!r}",
                        reply_to=protocol.text_of(msg, "id"),
                    )
                )
                await ws.close(code=1002)
                break
            # 未知类型只回错不断开：这是协议的前向扩展点
            await _handle_frame(ws, msg, device_id, trusted)
    except WebSocketDisconnect:
        # 设备离线是常态（移动网络/重启），不作为异常处理
        pass
    except Exception as exc:
        # 其他异常必须留痕：静默会让设备莫名掉线无从追查
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
            # 只有我这条连接确实是当前那条才算一次真离线。重连是新连接顶替旧连接，
            # 被顶替的那条随后走这里收尾——那一刻设备仍在线，不该记 offline。
            # 掉线原因由设备在重连后的 hello 里自报（last_offline_reason），服务端
            # 不再自行推断。
            if manager.unregister(device_id, ws):
                await _call(ws, devices_service.record_link_event, device_id, LINK_OFFLINE, "")
            await _call(ws, devices_service.set_device_offline, device_id)
            log_event("INFO", "gateway.device-disconnected", "设备断开", device_id=device_id)
            with _db_session(ws) as session:
                devices_service.publish_device_update(session, device_id)


async def _verify_device_token(ws: WebSocket) -> tuple[bool, str | None]:
    """握手前的设备凭据校验：返回 (放行, 令牌绑定的设备)。

    未开启校验时返回 (True, None)；开启后凭据必须有效，无效则回 1008 并拒绝。
    令牌绑定的设备标识交给握手阶段与 hello 自报比对——否则一枚合法令牌
    就能冒充任意一台设备。
    """
    if not settings.require_device_token:
        return True, None
    token = extract_bearer(ws.headers.get("authorization"))
    token_device_id: str | None = None
    if token is not None:
        # 会话生在死在线程函数里（WS 全路径的唯一例外面不在这里——与本文件
        # _call 的线程纪律一致）：事件循环上不开库
        def _authenticate() -> str | None:
            with ws.app.state.database.new_session() as auth_session:
                return devices_crud.authenticate_device(auth_session, token)

        token_device_id = await run_in_threadpool(_authenticate)
    if token_device_id is None:
        log_event("WARNING", "gateway.device-token-rejected", "设备令牌无效，连接被拒")
        await ws.close(code=1008)
        return False, None
    return True, token_device_id


async def _accept_hello(ws: WebSocket, token_device_id: str | None) -> tuple[str, bool, str] | None:
    """首帧必须是 link.hello：注册档案、回 hello_ack；失败已完成回错并返回 None。"""
    hello = protocol.parse(await ws.receive_text())
    if hello is None or hello.get("type") != LINK_HELLO:
        log_event("WARNING", "gateway.hello-required", "首帧必须是 link.hello，连接被拒")
        await ws.send_text(protocol.error(reason_codes.HELLO_REQUIRED.code, "首帧必须是 link.hello"))
        await ws.close()
        return None

    hello_payload = payload_of(hello)
    device_id = text_of(hello_payload, "device_id")
    if hello.get("v") != protocol.PROTOCOL_VERSION:
        # 版本不符等于双方说的是两套协议，继续握手没有意义：回错后断开
        # （1002，RFC 6455 的协议错误）。日志带设备自报标识，便于追是哪台设备升错了版。
        log_event(
            "WARNING",
            "gateway.version-mismatch",
            "hello 协议版本不符，连接被拒",
            device_id=device_id or "<unknown>",
            server_version=protocol.PROTOCOL_VERSION,
            frame_version=hello.get("v"),
        )
        await ws.send_text(
            protocol.error(
                reason_codes.VERSION_MISMATCH.code,
                f"服务端协议版本为 {protocol.PROTOCOL_VERSION}，收到的帧携带 {hello.get('v')!r}",
            )
        )
        await ws.close(code=1002)
        return None
    if token_device_id is not None and device_id != token_device_id:
        log_event("WARNING", "gateway.device-token-mismatch", "hello 设备与令牌绑定设备不一致")
        await ws.send_text(protocol.error(reason_codes.HELLO_REQUIRED.code, "设备与凭据不匹配"))
        await ws.close(code=1008)
        return None
    version_code = as_int(hello_payload.get("version_code"))
    row = await _call(ws, devices_service.get_device, device_id)
    if row is not None and row["retired"]:
        # 4403（应用私用段）：设备被除名。1008 会让设备清凭据重注册——对停用设备那是
        # 一个"enroll→拒绝→退避"的永久循环；4403 让它停下等人工。凭据无效仍走 1008。
        await ws.send_text(protocol.error(reason_codes.DEVICE_RETIRED.code, reason_codes.DEVICE_RETIRED.message))
        await ws.close(code=WS_CLOSE_DEVICE_REVOKED)
        return None
    trusted = bool(row and row["trusted"])
    # 经 Caddy 代理而来：真实设备 IP 在 X-Real-IP 头里
    ip = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
    # 开机时刻由服务端反推（此刻减设备上报的运行秒数）：运行时长是单调量，
    # 不受设备墙上时钟偏差影响
    booted_at = rfc3339_ago(as_int(hello_payload.get("uptime_s")))
    await _call(
        ws,
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
    # 下发→下载→安装→重启→再下发的无限重装循环。
    await _call(ws, ota_crud.mark_installed_upto, device_id, version_code)
    # 设备随 hello 带回的上一次断开原因（0.4.35+）：记进 online 事件的 reason
    last_offline_reason = text_of(hello_payload, "last_offline_reason")
    old_ws = manager.register(device_id, ws)
    if old_ws is not None:
        # 被顶替的旧连接必须关掉：校园网静默丢包时旧处理协程挂在 receive_text()
        # 上永不返回（TCP 层没有 FIN/RST，ASGI 层不抛 WebSocketDisconnect）。
        # 关闭旧 WS 让 receive_text() 当场抛，旧协程的 finally 才能执行——
        # 记 offline、刷 last_seen、写 device-disconnected 日志全在那一处。
        with contextlib.suppress(Exception):
            await old_ws.close(code=WS_CLOSE_GOING_AWAY)
    # 载入上次记下的钟偏差。从上线到首次 pong 之间有一小段窗口，不载入的话那段时间
    # 读到的是 0（等于不平移）——而刚接手、那边还被占着恰恰最常落在窗口里。
    await _call(ws, devices_service.load_clock_skew, device_id)
    await _call(ws, devices_service.record_link_event, device_id, LINK_ONLINE, last_offline_reason)
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
            LINK_HELLO_ACK,
            {
                "trusted": trusted,
                "server_time": now_rfc3339(),
                # 只给设备端消费的键（effective 再按 scope 过滤）：服务端自己的键
                # 不占设备的解析路径，两条下发的路（这里与 config.update）给的键集要一致。
                "config": await _call(ws, config_store.device_effective, device_id),
                "upgrade": await _call(ws, ota_crud.latest_for, device_id, version_code),
            },
            reply_to=text_of(hello, "id"),
        )
    )
    return device_id, trusted, ip


async def _handle_frame(
    ws: WebSocket,
    msg: JsonObject,
    device_id: str,
    trusted: bool,
) -> None:
    """分发一帧入站报文。未信任设备只放行链路层消息，业务帧静默忽略。"""
    mtype = text_of(msg, "type")
    frame_id = text_of(msg, "id")
    payload = payload_of(msg)

    # 存活证据的唯一实现：任何入站帧都刷新 last_seen（帧入口一处，不按帧类型各写）。
    # 锚点 ping 的周期（PING_INTERVAL_S）是空闲时刷新的上界。
    await _call(ws, devices_service.touch_seen, device_id)

    if mtype == LINK_PONG:
        # 用设备带回来的墙上时刻更新钟偏差。那个偏差把帧里的 open_time / in_time
        # 平移到服务端时间轴——那两个字段是"这次会话从什么时候开始"的唯一线索
        # （见 core/device_clock）。空闲时段偏差的维持全靠这条通路。
        #
        # `received_at` 在这一层取一次就往下传：函数执行已经晚于报文到达，让下游再取
        # 一次只会白添一个调用栈的误差。
        await _call(
            ws,
            devices_service.update_clock_skew,
            device_id,
            payload.get("device_time"),
            now_rfc3339(),
        )
    elif mtype == CONFIG_UPDATE_ACK:
        device_actions.resolve_config_ack(device_id, as_int(payload.get("revision")))
    elif mtype == DOOR_SNAPSHOT:
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
            await _call(
                ws,
                devices_service.update_clock_skew,
                device_id,
                device_clock_text,
                captured_at,
            )
        # 回执语义：**收到 ack ⇔ 帧已受理**。派生的失败已在 service 内部止住并照常返回
        # ok：回执让设备删掉发件箱那一行，队列继续流动，失败记入 doors.snapshot-derive-failed。
        # 扣下回执救不回什么——重发同一条帧不会得到不同的结果，漏掉的边沿由下一帧补上。
        result = await _call(ws, doors_service.process_snapshot, device_id, payload)
        # 帧是后台任务的心跳：门刚被占用、刚被释放，都只能从帧看出来。调度器据此重算这台
        # 设备每个喷头的"下一个到点"，并把待补的那次分配接上——**不轮询**。
        sessions_scheduler.on_frame(device_id)
        # 电源计划：hold 施加的钟跳会清空喷头，这里按施加瞬间记下的"谁在哪个喷头"把名单
        # 内的人重分配回原喷头。它同步返回，真正的分配在后台任务里跑——帧的处理路径上不能等命令
        power_service.on_frame(device_id)
        # 语音播报：同步建任务后立即返回；回执失败时设备会重发同一帧，event_key 去重。
        voice_service.schedule_events(result.events)
        await ws.send_text(
            protocol.envelope(
                DOOR_SNAPSHOT_ACK,
                {"snapshot_id": text_of(payload, "snapshot_id")},
                reply_to=frame_id,
            )
        )
    elif mtype == DEVICE_COMMAND_RESULT:
        if not trusted:
            return
        if not device_actions.resolve(text_of(msg, "reply_to"), payload):
            # 回执找不到对应的在途命令（多半是命令已超时收尾）：回错误帧保持
            # 协议完整性反馈，设备端只记日志，不产生副作用。
            await ws.send_text(
                protocol.error(reason_codes.UNKNOWN_COMMAND_ID.code, "没有与该结果对应的待办命令", reply_to=frame_id)
            )
    elif mtype == DEVICE_LOG_UPLOAD:
        if not trusted:
            return
        kind = text_of(payload, "kind")
        content = payload.get("content")
        if kind not in ("device", "crash", "ota") or not isinstance(content, str):
            await ws.send_text(protocol.error(reason_codes.BAD_JSON.code, "日志上送形状不对", reply_to=frame_id))
            return
        if len(content.encode("utf-8")) > 64 * 1024:
            await ws.send_text(protocol.error(reason_codes.BAD_JSON.code, "日志内容超过 64 KiB", reply_to=frame_id))
            return
        # 落库即回执（无需回帧）：失败回执反而让设备重发同一份大内容；真失败时下一轮
        # 握手会重新上送，日志是幂等的现态读数，不是队列
        await _call(ws, devices_service.record_log_upload, device_id, kind, content)
        log_event("INFO", "gateway.log-uploaded", "设备日志已上送", device_id=device_id, kind=kind)
    elif mtype == DEVICE_STATUS:
        if not trusted:
            return
        # 设备上报自己的电源计划事实：从浴室 App 参数文件读到的平台开关机时刻，
        # 与本机时区偏移。它们是 hold 触发/恢复换算与语音当地时刻的唯一来源——
        # 服务端不定任何时刻，全部自适应。落库即回执（无需回帧）；形状不对回错误帧
        # （与 device.log_upload 同一处置）。
        close = payload.get("auto_close_time")
        open_at = payload.get("auto_open_time")
        zone = payload.get("zone_offset_minutes")
        valid = (
            isinstance(close, str)
            and parse_time(close) is not None
            and isinstance(open_at, str)
            and parse_time(open_at) is not None
            and isinstance(zone, int)
            and not isinstance(zone, bool)
            and -720 <= zone <= 840
        )
        if not valid:
            await ws.send_text(protocol.error(reason_codes.BAD_JSON.code, "上报事实形状不对", reply_to=frame_id))
            return
        await _call(ws, devices_service.record_status_report, device_id, close, open_at, zone)
        log_event(
            "INFO",
            "gateway.status-reported",
            "设备电源计划事实已上报",
            device_id=device_id,
            close=close,
            open_at=open_at,
            zone=zone,
        )
    elif mtype == OTA_PROGRESS:
        if not trusted:
            return
        # 设备自更新的进度上报（一次性流程，设备端 AgentService 的 onProgress 发出）：
        # installing 通知电源计划的布防可见性（随后的进程重启掉线是预期表现，豁免），
        # 其余只记日志。它不落库——进度是一次性的中间态，收完即失效；"是否已安装"由
        # hello 的 version_code 回答（ota_release_install）。协议里有 Schema 而此前
        # 没有分支，设备的每一帧进度都会收到 proto.unknown_type 错误帧，两侧日志均
        # 记录对方的错误。
        if text_of(payload, "stage") == "installing":
            power_service.note_install_started(device_id)
        log_event(
            "INFO",
            "ota.progress-reported",
            "设备自更新进度",
            device_id=device_id,
            version=text_of(payload, "version"),
            stage=text_of(payload, "stage"),
            detail=text_of(payload, "detail"),
        )
    else:
        await ws.send_text(protocol.error(reason_codes.UNKNOWN_TYPE.code, f"不认识的消息类型：{mtype}"))


#: 锚点 ping 周期（秒）：把服务端时刻交给设备刷新时间锚点。周期取 5 分钟——
#: 锚龄内的偏差只有单调钟晶振漂移（8.5 秒/天，5 分钟漂 0.02 秒），远小于秒级
#: 刻度；链路判活不在此列，由 uvicorn 的 WS 协议层 ping 承担（默认 20 秒）。
#: 曾是配置键（运维从未改过，2026-11-17 精简收编为常量）。
PING_INTERVAL_S = 300


async def _anchor_ping_loop(ws: WebSocket, device_id: str) -> None:
    """按固定周期向设备发锚点 ping：把服务端时刻交给设备刷新时间锚点。

    ping 里带着服务端此刻的时刻，设备收到就更新自己的时间锚点
    （core/clock/ServerClock），它采集的每一帧都带上那个时刻。这是 link.ping
    的唯一职责——链路判活由 uvicorn 的 WS 协议层 ping 承担（ws_ping_interval
    默认 20 秒、pong 超时断连），app 层不再重复判活。

    发送失败（含 TCP 静默丢包后的 WS 关闭）时主动关闭 WS：让 handler 的
    receive_text() 当场抛 WebSocketDisconnect、finally 正常收尾——否则
    handler 挂在死连接上永不返回（校园网静默丢包不发 FIN/RST）。
    """
    while True:
        # 先等满一个周期再发首个 ping：刚握手成功的连接已在 hello_ack 里拿到锚点
        await asyncio.sleep(PING_INTERVAL_S)
        try:
            await ws.send_text(protocol.envelope(LINK_PING, {"sender_time": now_rfc3339()}))
        except (WebSocketDisconnect, RuntimeError) as exc:
            log_event(
                "DEBUG",
                "gateway.ping-loop-ended",
                "服务端锚点循环结束（链路已断）",
                device_id=device_id,
                error=str(exc),
            )
            # 关闭 WS 让 handler 的 receive_text() 解除阻塞——不关的话 handler
            # 永远等不到下一个帧（网络静默丢包，TCP 层没有 FIN/RST）
            with contextlib.suppress(Exception):
                await ws.close(code=WS_CLOSE_GOING_AWAY)
            return
            return
