"""免费名单领域的 API 契约。

名单在 HTTP 边界是**行**：读出来是行、交回去也是行。文件的行尾形状只在 service 里
拼（[service.render]），HTTP 边界与网页端都不碰它。
"""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import TypedDict

#: 一个学号：占满一整行，所以非空、且不含空白。
#:
#: 长度放到 32 而不按形状卡：设备上那份原件里混着 7 位的学号与 11 位的手机号，浴室 App
#: 两者都认——照它的实际取值来，不照"学号该长什么样"的想象来。
#:
#: **只收可打印 ASCII**。那份文件是给浴室 App 按字节读的，中文或全角字符写进去它认不
#: 出来——而认不出来不报错，表现只是"这个人没免费"。从 Word 或网页粘名单很容易带进
#: 全角字符，所以在入口拦下，不让它静默生效。
Student = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\x21-\x7e]+$", max_length=32)]


class FreelistRequest(BaseModel):
    """整份替换免费名单。

    空列表是合法输入：名单为空即无免费人员，这是设备上原件的既有语义（不是「未填写」）。
    """

    students: list[Student] = Field(description="名单，每行一个学号；空列表表示无免费人员")


class FreelistRead(TypedDict):
    """读免费名单的结果：命令结果三项 + 拆好的行。

    status 非 ok 时 students 为空，而**空列表不等于「读不到」**：空名单是一个合法且
    有意义的状态（无免费人员），两者混在一起会使调用方误以为名单已被清空。
    """

    status: str
    reason_code: str
    terminal_message: str
    students: list[str]
