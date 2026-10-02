"""会话领域的 API 契约：请求与响应模型集中于此。

请求模型与响应模型放同一处，前端生成类型只认一处。两种风格并存而非强行统一：

- 请求用 Pydantic 模型：它承担实际校验（字段范围、必填），错误经 FastAPI 转成
  problem+json 的 422；
- 响应用 TypedDict：响应不需要运行时校验，TypedDict 零开销，且 FastAPI 同样能
  据它生成 OpenAPI schema。

本文件不被任何领域逻辑依赖；`service` 反向从这里取响应模型，故无循环引用。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.core.protocol import JsonObject


class AssignRequest(BaseModel):
    """分配：把学号凭证分配到某个喷头。目标设备由路径给出，请求体只带业务参数。"""

    door_no: int = Field(ge=1)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SettleRequest(BaseModel):
    """结算：结束该学号在该设备上的进行中会话。目标设备由路径给出。"""

    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CommandOutcome(TypedDict):
    """设备命令的终态结果。

    对齐 protocol/schema/session.command_result.json。`data` 按命令而异
    （拍照回 image_base64、远程执行回 output），因此保持开放对象：前端按
    `action` 分支取字段，而非为每种命令各造一个响应类型。
    """

    status: str
    reason_code: str
    terminal_message: str
    data: JsonObject | None


class ConfigPushResult(TypedDict, total=False):
    """配置推送结果；`reason` 仅在下发或设备确认失败时出现。"""

    notified: bool
    applied: bool
    reason: str
