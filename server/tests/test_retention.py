"""保留期声明=实现的钉子。

各表 docstring 声明的保留天数与 audit prune 的 RETENTIONS 登记必须一致：
改声明不改登记（或反之）在这里当场红。业务永久表（会话/段/门状态迁移）
不在此列——它们的 docstring 写的是「永久保留」。
"""

from app.audit_middleware import RETENTIONS
from app.modules.devices.models import DeviceCredential, DeviceLinkEvent, DeviceLog
from app.modules.doors.models import DoorSnapshot
from app.modules.voice.models import VoiceDelivery
from app.persistence.audit import ApiCall, CommandAudit, JobRun


def test_every_declared_retention_is_registered() -> None:
    """声明了保留期的表都在 RETENTIONS 里，天数与声明一致。"""
    declared = {
        ApiCall: 90,
        CommandAudit: 90,
        JobRun: 180,
        DeviceLinkEvent: 90,
        DoorSnapshot: 30,
        VoiceDelivery: 90,
    }
    registered = {table: days for table, _column, days in RETENTIONS}
    assert registered == declared


def test_permanent_tables_are_not_registered() -> None:
    """永久保留的业务表不得进入清理登记（防止误删运营历史）。"""
    permanent = {DeviceCredential, DeviceLog}
    registered_tables = {table for table, _column, _days in RETENTIONS}
    assert registered_tables.isdisjoint(permanent)
