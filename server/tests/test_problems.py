"""问题类型登记表完整性：唯一 code、稳定文案、HTTP 错误面与登记表的一致性。"""

import re

import pytest
from fastapi.testclient import TestClient

from app.core import errors, problems


def test_registry_codes_are_unique_and_kebab_case() -> None:
    codes = [problem.code for problem in problems.REGISTRY]

    assert len(codes) == len(set(codes))
    for code in codes:
        assert re.fullmatch(r"[a-z][a-z0-9]+(-[a-z0-9]+)*", code), code


def test_registry_statuses_are_valid_http_codes() -> None:
    for problem in problems.REGISTRY:
        assert 400 <= problem.status < 600
        assert problem.title.strip()


def test_every_problem_constant_is_registered() -> None:
    """登记表外的 Problem 实例（模块常量）不允许存在：漏登记即漏客户端分支。"""
    constants = {
        name: value
        for name, value in vars(problems).items()
        if isinstance(value, problems.Problem) and name.isupper() and not name.startswith("_")
    }
    for name, problem in constants.items():
        assert any(problem is registered for registered in problems.REGISTRY), f"{name} 未登记进 REGISTRY"


def test_unregistered_status_gets_generated_type() -> None:
    """框架抛出的未登记状态码按状态码生成 type，无可分支语义。"""
    assert errors._unregistered(404).code == "http-404"
    assert errors._unregistered(599).code == "http-599"
    assert errors._unregistered(404).status == 404
    assert errors._unregistered(404).title == "请求失败"


def test_http_exception_is_rendered_as_problem_json(client: TestClient, fresh_db: str) -> None:
    """API 面的未登记 404 走 problem+json；非 API 的未知路径回 SPA 入口（history 路由刷新）。"""
    spa = client.get("/definitely-not-a-route")
    assert spa.status_code == 200
    assert "text/html" in spa.headers["content-type"]

    api_miss = client.get("/api/v1/definitely-not-a-route")
    assert api_miss.status_code == 404
    assert api_miss.headers["content-type"].startswith("application/problem+json")
    body = api_miss.json()
    assert body["type"].endswith("/problems/http-404")
    assert {"type", "title", "status", "detail"} <= set(body)


def test_unhandled_exception_returns_500_and_logs_the_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """未登记异常的最后防线：500 统一形状，栈与路径进日志而非框架默认 detail。

    Starlette 的 ServerErrorMiddleware 发出响应后会把异常重新抛出（供服务器层记
    日志），共享 client 夹具因此拿到的是重抛；这里用独立小应用关掉重抛，直接
    钉响应体与日志两样产物。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.errors import install_error_handlers
    from app.modules.doors import service as doors_service

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.core.logging.log_event", lambda _l, event, _m, **attrs: events.append({"event": event, **attrs})
    )

    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/boom")
    def route() -> list[object]:
        return doors_service.list_doors(None, None)  # type: ignore[arg-type]

    def explode(*_args: object, **_kwargs: object) -> object:
        message = "exploded"
        raise RuntimeError(message)

    monkeypatch.setattr(doors_service, "list_doors", explode)

    with TestClient(probe, raise_server_exceptions=False) as probe_client:
        response = probe_client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    logged = [event for event in events if event["event"] == "http.unhandled"]
    assert logged
    assert "RuntimeError('exploded')" in str(logged[0]["error"])
    assert logged[0]["path"] == "/boom"


def test_api_problem_error_carries_problem_and_detail() -> None:
    error = problems.ApiProblemError(problems.DEVICE_NOT_FOUND, "设备 dev-x")

    assert error.problem is problems.DEVICE_NOT_FOUND
    assert error.detail == "设备 dev-x"
    assert "device-not-found" in str(error)


def test_shared_wording_between_problem_and_reason_registries() -> None:
    """同一件事在 HTTP 层与命令回报层应当是同一句话。"""
    from app.core import reason_codes

    assert problems.DOOR_NOT_FREE.title == reason_codes.DOOR_NOT_FREE.message
    assert problems.DEVICE_OFFLINE.title == "设备离线"
    assert reason_codes.describe("device.offline") == "设备离线"


def test_problem_type_uris_are_resolvable(client: TestClient, fresh_db: str) -> None:
    """RFC 9457 type URI（/problems/<code>）必须可解析：已登记的回登记信息，
    未登记的按未找到的资源处理（404 problem+json）。"""
    doc = client.get("/problems/device-not-found")
    assert doc.status_code == 200
    assert doc.json() == {"code": "device-not-found", "title": "找不到设备", "status": 404}

    miss = client.get("/problems/no-such-code")
    assert miss.status_code == 404
    assert miss.headers["content-type"].startswith("application/problem+json")
