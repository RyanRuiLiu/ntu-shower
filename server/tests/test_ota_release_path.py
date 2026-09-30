"""发布文件路径构造的安全边界：版本号受限，杜绝目录穿越（CWE-22）。

版本号最终参与文件路径拼接，因此它是一段不可信输入；本测试锁定
"非法版本号在 service 层也被拒绝"这一纵深防御，而不只依赖 HTTP 边界校验。
"""

import pytest

from app.modules.ota import service


@pytest.mark.parametrize(
    "malicious",
    [
        "../../etc/passwd",
        "..",
        ".",
        "0.2.5/../../secret",
        "0.2.5/apk",
        "0.2",
        "0.2.5.6",
        "v0.2.5",
        "0.2.5 ",
        "0.2.5\n",
        "",
        "0.2.5;id",
    ],
)
def test_release_path_rejects_illegal_version(malicious: str) -> None:
    with pytest.raises(service.InvalidVersionError):
        service.release_path(malicious)


def test_release_path_accepts_semver() -> None:
    path = service.release_path("0.3.1")
    assert path.name == "0.3.1.apk"
    assert path.parent.name == "ota_releases"
