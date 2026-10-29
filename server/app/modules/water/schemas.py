"""水控板直控的请求模型。

目标设备在路径里，请求体只放逻辑喷头号。板 IP/端口/起始门号/门数不在请求里——
它们来自设备上的 `data.xml` waterList，由设备端解析；服务端和网页都不接触板子坐标。
"""

from pydantic import BaseModel, Field


class WaterDoorRequest(BaseModel):
    """开阀/关阀：一个逻辑喷头号。"""

    door_no: int = Field(ge=1)
