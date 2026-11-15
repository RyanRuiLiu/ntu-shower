"""免费名单：读设备上那份名单、整份写回。

这一层盯的是**转换**与**请求的形状**：名单在 HTTP 边界是行、到设备端是文件原文，中间
那次转换只在一处发生（`freelist.service`）。设备动作换成桩——真下发要一条在线的设备
链路，那是 `test_action_names` 与设备端单测的活；动作名两侧一致由那个对账测试保证。
"""

import pytest
from fastapi.testclient import TestClient

from app.core import actions
from app.modules.freelist import routes, service

DEVICE = "dev-freelist"

#: 设备上那份原件的形状：CRLF，且最后一行也带。下面几条断言都按它来，因为"按原样"就是
#: 这个功能的全部正确性所在——浴室 App 怎么读它我们没看过源码。
SAMPLE = "1992045\r\n13962727553\r\n66666666\r\n"

#: 设备回的成功结果。`freelist.write` 成功时不回 data（见 core/outcomes 的 ACTION_DATA）。
OK_RESULT: dict[str, object] = {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}


def _stub_action(
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object] | None = None,
    sent: list[tuple[str, dict[str, str]]] | None = None,
) -> None:
    """把设备动作换成桩：记下下发的内容，返回给定的结果。"""

    async def fake(
        _session: object, _device_id: str, action: str, params: dict[str, str], _timeout_s: float, **_kw: object
    ) -> object:
        if sent is not None:
            sent.append((action, params))
        return OK_RESULT if result is None else result

    monkeypatch.setattr(routes, "device_action", fake)


def _readable() -> dict[str, object]:
    """设备上读得到那份原件时的结果。"""
    return {**OK_RESULT, "data": {"content": SAMPLE}}


# ── 两种表示之间的转换 ────────────────────────────────────────────────────


def test_render_writes_crlf_and_ends_with_it() -> None:
    """行尾是 CRLF 且最后一行也带——设备上那份原件就是 17 个 CR 配 17 个 LF。"""
    assert service.render(["1992045", "66666666"]) == "1992045\r\n66666666\r\n"
    assert service.render([]) == ""


def test_parse_drops_the_blank_line_a_trailing_crlf_leaves() -> None:
    assert service.parse(SAMPLE) == ["1992045", "13962727553", "66666666"]


def test_parse_tolerates_lf_only_and_stray_blank_lines() -> None:
    """原件若被人手工改过（换了行尾、插了空行），读出来的仍是学号，不带空项。"""
    assert service.parse("1992045\n\n66666666\n") == ["1992045", "66666666"]


def test_the_round_trip_returns_the_original_bytes() -> None:
    """读出来再写回去不该改动文件——这是"只改内容、不动格式"的可执行说法。"""
    assert service.render(service.parse(SAMPLE)) == SAMPLE


# ── 读 ────────────────────────────────────────────────────────────────────


def test_read_returns_the_rows(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_action(monkeypatch, result=_readable())

    response = client.get(f"/api/v1/devices/{DEVICE}/freelist", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["students"] == ["1992045", "13962727553", "66666666"]


def test_read_of_an_unreadable_file_reports_the_reason(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """读不到时行是空的，但**空的 students 不等于空名单**：status 与原因码要一起看。"""
    _stub_action(
        monkeypatch,
        result={
            "status": "failed",
            "reason_code": "device.freelist_read_failed",
            "terminal_message": "读取免费名单失败：no such file",
            "data": None,
        },
    )

    response = client.get(f"/api/v1/devices/{DEVICE}/freelist", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["students"] == []
    assert body["reason_code"] == "device.freelist_read_failed"
    assert body["terminal_message"]


def test_read_needs_admin(client: TestClient) -> None:
    assert client.get(f"/api/v1/devices/{DEVICE}/freelist").status_code == 401


# ── 写 ────────────────────────────────────────────────────────────────────


def test_write_sends_the_crlf_text(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, dict[str, str]]] = []
    _stub_action(monkeypatch, sent=sent)

    response = client.put(
        f"/api/v1/devices/{DEVICE}/freelist",
        headers=admin_headers,
        json={"students": ["1992045", "66666666"]},
    )

    assert response.status_code == 200
    assert sent == [(actions.FREELIST_WRITE, {"content": "1992045\r\n66666666\r\n"})]


def test_write_of_an_empty_list_is_allowed(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """空名单是合法状态（没人免费），不是"没填"——所以它照常下发。"""
    sent: list[tuple[str, dict[str, str]]] = []
    _stub_action(monkeypatch, sent=sent)

    response = client.put(f"/api/v1/devices/{DEVICE}/freelist", headers=admin_headers, json={"students": []})

    assert response.status_code == 200
    assert sent == [(actions.FREELIST_WRITE, {"content": ""})]


#: 后两个是**全角**：从 Word 或网页粘名单常把数字带成全角，而浴室 App 按字节读它认不
#: 出来——认不出来又不报错，表现只是"这个人没免费"。所以在入口拦下，不让它静默生效。
@pytest.mark.parametrize("student", ["", "   ", "199 2045", "1992045\n66666666", "２５３０１", "张三", "x" * 33])
def test_write_rejects_anything_that_is_not_one_student_per_line(
    client: TestClient, admin_headers: dict[str, str], student: str
) -> None:
    """一行一个学号是这份文件的形状：空行与带空白的值写进去会变成一个坏行。"""
    response = client.put(
        f"/api/v1/devices/{DEVICE}/freelist",
        headers=admin_headers,
        json={"students": [student]},
    )

    assert response.status_code == 422


def test_write_strips_what_the_editor_left_around_a_student(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """从表格里粘过来常带首尾空格：那不该算一次输入错误。"""
    sent: list[tuple[str, dict[str, str]]] = []
    _stub_action(monkeypatch, sent=sent)

    client.put(f"/api/v1/devices/{DEVICE}/freelist", headers=admin_headers, json={"students": [" 1992045 "]})

    assert sent == [(actions.FREELIST_WRITE, {"content": "1992045\r\n"})]


# ── 读写分离（与 water 域同一模式：read 与 write 是两枚独立 scope） ──────────


def test_read_scope_can_read_but_not_write(client: TestClient, fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """只授 freelist:read 的密钥：GET 可用，PUT 403——最小权限不含写。"""
    from tests.identity import bearer, register_key

    _stub_action(monkeypatch, result=_readable())
    headers = bearer(register_key("freelist-reader", ["freelist:read"], device_scope=["*"]))

    assert client.get(f"/api/v1/devices/{DEVICE}/freelist", headers=headers).status_code == 200
    put = client.put(f"/api/v1/devices/{DEVICE}/freelist", headers=headers, json={"students": []})
    assert put.status_code == 403
    assert put.json()["type"].endswith("/problems/insufficient-scope")


def test_write_scope_can_write_but_not_read(client: TestClient, fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """只授 freelist:write 的密钥：PUT 可用，GET 403——写位不再顺带送读位。"""
    from tests.identity import bearer, register_key

    _stub_action(monkeypatch, sent=[])
    headers = bearer(register_key("freelist-writer", ["freelist:write"], device_scope=["*"]))

    assert client.put(f"/api/v1/devices/{DEVICE}/freelist", headers=headers, json={"students": []}).status_code == 200
    get = client.get(f"/api/v1/devices/{DEVICE}/freelist", headers=headers)
    assert get.status_code == 403
    assert get.json()["type"].endswith("/problems/insufficient-scope")
