"""会话领域的 API 请求契约。

请求用 Pydantic 模型：它承担实际校验（字段范围、必填），错误经 FastAPI 转成
problem+json 的 422。响应形状是命令结果（CommandOutcome，见 app.contracts），
不需要本文件另立一份。
"""

from pydantic import BaseModel, Field


class AssignRequest(BaseModel):
    """分配：把学号凭证分配到某个喷头。目标设备由路径给出，请求体只带业务参数。"""

    door_no: int = Field(ge=1)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SettleRequest(BaseModel):
    """结算：结束该学号在该设备上的进行中会话。目标设备由路径给出。"""

    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)
