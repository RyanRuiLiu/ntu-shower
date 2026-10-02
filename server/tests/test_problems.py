"""问题类型登记表完整性：唯一 code、稳定文案、HTTP 错误面与登记表的一致性。"""

import re

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
    """未登记的框架问题同样走 problem+json（RFC 9457 覆盖所有错误响应）。"""
    r = client.get("/definitely-not-a-route")

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/problems/http-404")
    assert {"type", "title", "status", "detail"} <= set(body)


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
