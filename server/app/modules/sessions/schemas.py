"""会话领域的 API 契约：请求与响应模型集中于此。

为什么单独一层：请求模型在 routes、响应模型在 service 时，同一个契约要翻两处
才能看全，前端生成类型也只认一处。

为什么两种风格并存，而不是强行统一：

- **请求**用 Pydantic 模型——它承担实际校验（字段范围、必填），错误经 FastAPI
  转成 problem+json 的 422；
- **响应**用 TypedDict——响应不需要运行时校验，TypedDict 零开销，且 FastAPI
  同样能据它生成 OpenAPI schema。

本文件不被任何领域逻辑依赖；`service` 反向从这里取响应模型，故无循环引用。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.core.protocol import JsonObject


class AssignRequest(BaseModel):
    """分配：把某个学号凭证分配到某台设备的某个喷头。"""

    device_id: str = Field(min_length=8)
    door_no: int = Field(ge=1)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SettleRequest(BaseModel):
    """结算：结束该学号在该设备上的进行中会话。"""

    device_id: str = Field(min_length=8)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PhotoRequest(BaseModel):
    """拍照：抓取一次现场画面。"""

    device_id: str = Field(min_length=8)


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
