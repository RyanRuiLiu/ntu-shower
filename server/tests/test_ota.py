"""OTA 发布注册表测试：常规升级判定、force 调试发布、target 过滤。"""

from types import ModuleType

import pytest

from app.modules.ota import repository


@pytest.fixture
def repo(fresh_db: str) -> ModuleType:
    repository.init_tables()
    return repository


def test_normal_release_applies_when_newer(repo: ModuleType) -> None:
    repo.insert("0.2.0", 2, "a" * 64, False, False, "all")
    rel = repo.latest_for("dev-1", 1)
    assert rel is not None
    assert rel["version"] == "0.2.0"
    assert repo.latest_for("dev-1", 2) is None  # 同版本号不重复推


def test_force_release_ignores_version(repo: ModuleType) -> None:
    repo.insert("0.2.0", 2, "b" * 64, False, True, "dev-1")
    rel = repo.latest_for("dev-1", 2)  # 版本号相同，force 仍适用
    assert rel is not None
    assert rel["force"] is True


def test_target_filters_devices(repo: ModuleType) -> None:
    repo.insert("0.3.0", 3, "c" * 64, False, False, "dev-1,dev-2")
    assert repo.latest_for("dev-1", 1) is not None
    assert repo.latest_for("dev-2", 1) is not None
    assert repo.latest_for("dev-3", 1) is None  # 未在目标列表
    assert repo.latest_for("dev-9", 1) is None  # all 发布不存在时不适用


def test_force_higher_version_wins_over_normal(repo: ModuleType) -> None:
    repo.insert("0.2.0", 2, "a" * 64, False, False, "all")
    repo.insert("0.2.0", 2, "b" * 64, False, True, "dev-1")
    rel = repo.latest_for("dev-1", 1)
    assert rel is not None
    assert rel["force"] is True


def test_list_releases(repo: ModuleType) -> None:
    repo.insert("0.2.0", 2, "a" * 64, False, False, "all")
    assert len(repo.list_releases()) == 1


def test_force_release_no_loop_and_rearm(repo: ModuleType) -> None:
    """force 发布安装确认后不再推送（防死循环）；rearm 后重新生效。"""
    from app.modules.ota import repository as ota_repository

    # 同 code 的 force 发布验证防循环语义：
    ota_repository.insert("0.2.5x", 7, "c" * 64, False, True, "dev-loop")
    ota_repository.mark_installed_upto("dev-loop", 7)
    assert ota_repository.latest_for("dev-loop", 7) is None  # 已确认，不再推

    assert ota_repository.rearm("dev-loop", "0.2.5x") is True
    rel = ota_repository.latest_for("dev-loop", 7)
    assert rel is not None
    assert rel["version"] == "0.2.5x"

    # 常规（非 force）不受安装确认影响
    ota_repository.insert("0.3.0", 8, "d" * 64, False, False, "dev-loop")
    rel = ota_repository.latest_for("dev-loop", 7)
    assert rel is not None
    assert rel["version"] == "0.3.0"

    # 下架
    assert ota_repository.delete_release("0.2.5x") is True
    assert ota_repository.delete_release("0.2.5x") is False


def test_force_lower_version_offered_when_not_installed(repo: ModuleType) -> None:
    """force 发布低于设备版本也推送（调试重推语义），安装确认后停止。"""
    from app.modules.ota import repository as ota_repository

    ota_repository.insert("0.1.0x", 1, "e" * 64, False, True, "dev-f2")
    rel = ota_repository.latest_for("dev-f2", 7)
    assert rel is not None
    assert rel["version"] == "0.1.0x"
    ota_repository.mark_installed_upto("dev-f2", 7)
    assert ota_repository.latest_for("dev-f2", 7) is None
