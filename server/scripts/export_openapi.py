"""导出 OpenAPI 契约，供前端生成 TypeScript 类型。

前端的 API 类型不手写——手写等于把服务端契约抄一遍，必然漂移。
本脚本是那条链路的源头：

    python scripts/export_openapi.py && cd ../web && pnpm gen:types

契约变更后重跑即可，前端类型随之更新；`web/openapi.json` 入库，
这样 CI 无需启动服务端也能校验一致性。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent.parent / "web" / "openapi.json"


def main() -> int:
    app = create_app()
    spec = app.openapi()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    paths = len(spec.get("paths", {}))
    schemas = spec.get("components", {}).get("schemas", {})
    print(f"已写入 {OUTPUT}")
    print(f"  路径 {paths} 个 · 组件 schema {len(schemas)} 个")

    # 关键契约的抽查：这几项缺失说明服务端装配出了问题，早失败好过前端拿到残缺类型
    door_state = schemas.get("DoorState", {}).get("enum")
    if not door_state:
        print("错误：DoorState 枚举未出现在 OpenAPI 中——前端将无法生成状态联合类型", file=sys.stderr)
        return 1
    print(f"  DoorState: {door_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
