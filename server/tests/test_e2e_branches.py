"""根静态挂载排在所有路由之后：`/api`、`/ops`、`/ota`、`/gateway` 不会被它抢走。

Starlette 按注册顺序匹配。`mount("/")` 若排在路由之前，这些前缀会落进静态文件查找
并返回 404——客户端看到的只是"接口不存在"，与真的没有这条路由无法区分。

**路径取自运行时的 OpenAPI，不在这里硬编码**：硬编码的路径会随前缀迁移而过期
（`/ops/config/spec` 迁到 `/api/v1/config/spec` 就是一次），那时测试报的是它自己的
陈旧，而不是真实故障。`/gateway/device` 是 WebSocket 端点，不出现在 OpenAPI 里，
它由结构断言覆盖——那条不依赖任何路径。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.main import app


def _one_real_path_per_prefix() -> tuple[str, ...]:
    """每条对外前缀取一个真实存在的 HTTP 路径。"""
    paths = app.openapi()["paths"]
    picked = []
    for prefix in ("/api/v1/", "/ota/"):
        match = sorted(path for path in paths if path.startswith(prefix))
        assert match, f"{prefix} 下没有任何端点"
        picked.append(match[0])
    return tuple(picked)


def test_root_mount_is_registered_last() -> None:
    """静态挂载必须是最后一条路由，否则排在它后面的路径永远匹配不到。"""
    mount_at = [index for index, route in enumerate(app.routes) if isinstance(route, Mount)]
    assert mount_at, "根静态挂载不存在"
    trailing = [type(route).__name__ for route in app.routes[max(mount_at) + 1 :]]
    assert not trailing, f"静态挂载之后还有路由 {trailing}，它们会被静态文件查找挡住"


@pytest.mark.parametrize("path", _one_real_path_per_prefix())
def test_prefix_reaches_its_router(client: TestClient, path: str) -> None:
    """不是 404 即证明路由先于静态挂载匹配。"""
    response = client.get(path)
    assert response.status_code != 404, f"{path} 落进了静态文件查找"
