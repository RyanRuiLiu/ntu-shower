"""后台任务登记域的 API 契约。

`TaskView` 是用户面的读模型：名单里的密码、别人的学号一概不出现。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class TaskView(TypedDict):
    """一个后台任务此刻的状态，**按传入的学号**回答。

    `enabled` 是管理员在配置页设的全局开关，**只读**——登记与它无关：它关着的时候登记
    照样成立，只是不生效。`joined` 是这个学号在不在名单里。

    `description` 是这个功能干什么的一段话，**里面的时长与时刻是按当前生效的配置现填的**。
    客户端直接显示，不要自己再拼一份——那些值是可配的（`auto_settle_pause_threshold_s`
    之类），写死的那份迟早与真正在跑的参数不一致。
    """

    key: str
    label: str
    description: str
    enabled: bool
    joined: bool


class TaskMembershipRequest(BaseModel):
    """登记或取消登记。

    学号与密码都由调用方给出：服务端不校验这个学号是谁的，也不校验密码的真伪（理由见
    `service` 的模块注释）。取消登记也要求带密码，那只是为了形状一致——**能不能改名单
    的门槛是能力位，不是密码**。
    """

    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)
    joined: bool
