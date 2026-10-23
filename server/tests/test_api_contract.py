"""对外 HTTP 面统一协议的代码级约束：从 OpenAPI 契约反查，不从实现反查。

FastAPI 从各域 schemas 的模型生成 OpenAPI，本测试锁死两条规则：

1. `/api/v1` 下所有带 JSON 成功响应的端点，响应必须是**具名模型**（或具名模型的
   数组）——不允许裸对象。端点作者想返回什么，就必须在 schemas 里给出形状；
   想绕过模型直接拼 dict，测试立刻红。
2. 设备动作族（`POST /devices/{device_id}/<动词>`）的响应形状唯一：命令动作共用
   `CommandOutcome`，桥的两个端点例外（它们不等待设备命令结果，返回各自的报告）。

历史上 `GET /devices` 与 `GET /config/spec` 曾返回无形状的裸对象——契约缺失的
表现是调用方各自猜字段。这条规则之后由本测试看守。
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app

_SCHEMA = app.openapi()
_COMPONENTS = _SCHEMA.get("components", {}).get("schemas", {})

#: 桥端点不等待设备命令结果（本地建桥/回收），返回各自的报告而非 CommandOutcome。
_BRIDGE_REPORTS = {"BridgeOpened", "BridgeClosed"}


def _resolve_ref(schema: dict) -> str:
    """响应 schema 的组件名；非 $ref 返回空串。"""
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    return ""


def _iter_api_v1_operations() -> Iterator[tuple[str, str, dict[str, object]]]:
    for path, methods in _SCHEMA["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in methods.items():
            if method in ("get", "post", "put", "delete", "patch"):
                yield path, method, operation


#: APK 下载端点返回二进制流（FileResponse + Content-Disposition），JSON schema
#: 对它不适用——豁免是显式的，防止未来有别的端点借着这条溜过去。
_BINARY_PATHS = {"/api/v1/ota/releases/{version}/download"}


def test_every_api_v1_response_is_a_named_model() -> None:
    """/api/v1 的每个 JSON 成功响应都必须解析到具名模型（数组元素也算）。"""
    for path, method, operation in _iter_api_v1_operations():
        if path in _BINARY_PATHS:
            continue
        response = operation["responses"].get("200")
        if response is None:
            # 204 等无内容响应（如身份撤销）：没有可约束的形状
            continue
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        assert schema, f"{method.upper()} {path} 的 200 响应没有 schema（裸对象）"
        if schema.get("type") == "array":
            item = _resolve_ref(schema.get("items", {}))
            assert item, f"{method.upper()} {path} 的数组元素不是具名模型：{schema.get('items')}"
            assert item in _COMPONENTS, f"{method.upper()} {path} 引用了不存在的模型：{item}"
        else:
            ref = _resolve_ref(schema)
            assert ref, f"{method.upper()} {path} 的响应不是具名模型：{schema}"
            assert ref in _COMPONENTS, f"{method.upper()} {path} 引用了不存在的模型：{ref}"


def test_device_action_posts_share_command_outcome() -> None:
    """设备动作族的响应形状唯一：命令动作一律 CommandOutcome，桥端点例外。"""
    for path, methods in _SCHEMA["paths"].items():
        if "/devices/{device_id}/" not in path:
            continue
        post = methods.get("post")
        if post is None:
            continue
        schema = post["responses"]["200"]["content"]["application/json"]["schema"]
        ref = _resolve_ref(schema)
        allowed = {"CommandOutcome"} | _BRIDGE_REPORTS
        assert ref in allowed, f"POST {path} 的响应不是命令动作族的统一形状：{ref}"


def test_contract_models_exist_for_every_client_facing_row() -> None:
    """客户端直接消费的行模型必须真的登记在组件里（防模型定义了却没挂到端点上）。"""
    for expected in (
        "DeviceListItem",
        "DeviceRecord",
        "DoorView",
        "IdentityCatalog",
        "IdentityRecord",
        "TaskView",
        "ConfigSpecCatalog",
        "GlobalConfigView",
        "DeviceConfigView",
        "OtaManifest",
        "ReleaseRecord",
        "ApiCallRecord",
        "CommandAuditRecord",
        "CommandOutcome",
        "VoiceEventView",
        "VoicePlaceholderView",
        "VoiceSpecView",
        "VoiceRuleView",
        "VoicePreviewView",
        "VoiceStatusView",
        "VoiceDeliveryView",
    ):
        assert expected in _COMPONENTS, f"契约模型缺失：{expected}"


@pytest.fixture
def _openapi_served(client: TestClient) -> None:
    """OpenAPI 文档经应用本身可取：它就是客户端生成类型的来源。"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["paths"], "OpenAPI 文档为空"


@pytest.mark.usefixtures("_openapi_served")
def test_openapi_is_served_by_the_app() -> None:
    """文档能生成（本模块顶部的 `app.openapi()`）不等于它经应用可取。

    少了这一条，静态挂载抢走 `/openapi.json` 这类事故不会被任何用例发现——而它是
    客户端生成类型的来源。断言在 fixture 里，这里只负责把它挂进测试收集。
    """
