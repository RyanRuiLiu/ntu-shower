"""水控板术语与界面文案的跨端契约测试。

性质：契约测试（contract testing，行业标准分类）——锁的是三端（网页 JS、Android
Kotlin、ADR 证据记录）对同一组用户可见术语的共同约定，防"改一端漏另一端"。领域词汇
的单一出处对应 DDD 的 Ubiquitous Language（Evans）；跨实现核对是契约测试的标准形态。
这些词是网页与设备端对外可见的正名；只改一处、忘了另一处，用户就会看到两套说法。
状态映射本身由 `core/water.js` 的纯函数负责，这里锁定标签、按钮、计时对应关系
和不许回流的旧词。
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WATER = REPO / "server/web/core/water.js"
OVERVIEW = REPO / "server/web/views/overview.js"
APP = REPO / "server/web/app.js"
HANDLER = REPO / "android/app/src/main/java/com/ryanliu/ntushower/features/water/WaterHandler.kt"
WATER_EVIDENCE = REPO / "docs/adr/0015-water-board-evidence.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_five_verified_states_and_three_non_states() -> None:
    src = read(WATER)
    for label in (
        "已关闭",
        "待按电磁阀",
        "待授权",
        "待复位",
        "出水",
        "未验证状态",
        "未定义位",
        "未读取",
    ):
        assert f"label: '{label}'" in src, f"缺状态标签：{label}"
    assert "parts.join" not in src, "状态标签不许再按位名拼接"
    assert "WATER_STATE.unverified" in src
    assert "WATER_STATE.undefinedBits" in src


def test_copy_single_source_and_no_old_buttons() -> None:
    src = read(WATER)
    for text in (
        "authorize: '授权出水'",
        "cut: '断水'",
        "authorizeTitle: '置授权位，不建会话；不直接出水，现场按下电磁阀才出水'",
        "cutTitle: '清授权位与出水位，立即断水；不写浴室 App 会话，按钮位保留'",
        "authorizeWaitButton: '已授权出水；现场按下电磁阀才出水'",
        "authorizeWaitReset: '已授权出水；电磁阀按钮已开，需按两次电磁阀才出水'",
        "cutButtonOn: '已断水；电磁阀按钮仍开，现场再按一次才回到已关闭'",
    ):
        assert text in src, f"缺文案：{text}"
    assert "只登记" not in src


def test_buttons_and_status_come_from_the_shared_module() -> None:
    src = read(OVERVIEW)
    controls = src.split("function doorWaterControls", 1)[1].split("function renderKpi", 1)[0]
    assert "waterStatusText(found.door.status)" in controls
    assert "waterStatusTitle(found.door.status)" in controls
    assert "WATER_COPY.authorize" in controls
    assert "WATER_COPY.cut" in controls
    assert "canAuthorize(" in controls
    assert "canCut(" in controls
    # 卡片这一段不许再出现旧按钮名或位名拼接
    assert ">开阀<" not in controls
    assert ">关阀<" not in controls
    assert "只登记" not in controls


def test_timers_use_the_measured_fields() -> None:
    src = read(OVERVIEW)
    assert "const flow = found ? fmtDuration(found.door.allopen_s)" in src
    assert "const auth = found ? fmtDuration(found.door.bath_s)" in src
    flow_line = next(line for line in src.splitlines() if "水控板淋浴时长" in line)
    auth_line = next(line for line in src.splitlines() if "水控板授权计时" in line)
    assert "${esc(flow)}" in flow_line
    assert "${esc(auth)}" in auth_line
    assert src.index("水控板授权计时") < src.index("水控板淋浴时长")
    assert "水控板开阀时长" not in src


def test_device_info_rows() -> None:
    src = read(APP)
    assert "['喷头数', used]" in src
    assert "WATER_COPY.notRead" in src
    assert "WATER_COPY.failed" in src


def test_device_terminal_messages() -> None:
    src = read(HANDLER)
    assert 'if (open) "已授权出水" else "已断水"' in src
    assert "开阀" not in src
    assert "关阀" not in src


def test_decisions_water_section_has_no_old_terms() -> None:
    section = read(WATER_EVIDENCE)
    for old in ("开阀位", "电磁阀按钮位", "物理开关", "被动同步", "只登记", "登记占用"):
        assert old not in section, f"现场实测节仍有旧词：{old}"
    for word in ("授权位", "按钮位", "出水位", "授权出水", "断水", "水控板授权计时", "水控板淋浴时长"):
        assert word in section, f"现场实测节缺正名：{word}"
