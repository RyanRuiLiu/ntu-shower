"""OTA 发布保留：只留最近 N 个，注册行与 APK 文件一起裁。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.modules.ota import repository
from app.modules.ota import service as ota_service


@pytest.fixture
def ota_dir(fresh_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """独立的发布文件目录。依赖 fresh_db：注册表查询必须落在临时库上，
    否则会读写到本地开发库。"""
    path = tmp_path / "releases"
    monkeypatch.setattr(config_module.settings, "ota_dir", str(path))
    return path


def _publish(version: str, code: int) -> None:
    ota_service.store_release(version, f"apk-of-{version}".encode())
    repository.insert_release(version, code, "0" * 64, mandatory=False, force=False, target="all")


def test_prune_keeps_newest_and_removes_file_and_row(ota_dir: Path) -> None:
    _publish("0.1.0", 1)
    _publish("0.1.1", 2)
    _publish("0.1.2", 3)

    removed = ota_service.prune_releases(keep=2)

    assert removed == ["0.1.0"]  # version_code 最旧的那个
    assert not (ota_dir / "0.1.0.apk").exists()
    assert (ota_dir / "0.1.1.apk").exists()
    assert (ota_dir / "0.1.2.apk").exists()
    versions = [rel["version"] for rel in repository.list_releases()]
    assert versions == ["0.1.2", "0.1.1"]


def test_prune_with_keep_larger_than_count_is_noop(ota_dir: Path) -> None:
    _publish("0.1.0", 1)

    assert ota_service.prune_releases(keep=5) == []
    assert (ota_dir / "0.1.0.apk").exists()


def test_orphan_files_are_swept(ota_dir: Path) -> None:
    """盘上有、注册表里没有的孤儿 APK 一并清除——"不积累垃圾"只做一半等于没做。"""
    ota_dir.mkdir(parents=True, exist_ok=True)
    (ota_dir / "0.0.9.apk").write_bytes(b"orphan")
    _publish("0.1.0", 1)

    removed = ota_service.prune_releases(keep=1)

    assert "0.0.9" in removed
    assert not (ota_dir / "0.0.9.apk").exists()
    assert (ota_dir / "0.1.0.apk").exists()


def test_delete_route_removes_file_too(client: TestClient, ota_dir: Path) -> None:
    _publish("0.1.0", 1)
    r = client.delete("/ops/releases/0.1.0", headers={"X-Ops-Token": "test-token"})
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert not (ota_dir / "0.1.0.apk").exists()
