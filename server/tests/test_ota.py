"""OTA：manifest、下载、发布上传、target 过滤、force 不重推、rearm、保留裁剪与路径安全。"""

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import config as config_module
from app.modules.ota import repository, service


@pytest.fixture
def ota_dir(fresh_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """发布文件目录已随 fresh_db 落在临时区，这里取路径便于断言。"""
    return Path(config_module.settings.ota_dir)


def _publish(version: str, code: int, *, force: bool = False, target: str = "all") -> None:
    service.store_release(version, f"apk-of-{version}".encode())
    repository.insert_release(version, code, "0" * 64, mandatory=False, force=force, target=target)


# ── 仓储层：发布判定 ──


def test_latest_for_respects_version_and_target(fresh_db: str) -> None:
    _publish("0.2.0", 2, target="dev-1,dev-2")

    rel = repository.latest_for("dev-1", 1)
    assert rel is not None
    assert rel["version"] == "0.2.0"
    assert repository.latest_for("dev-1", 2) is None  # 同版本号不重复推
    assert repository.latest_for("dev-3", 1) is None  # 未在目标列表


def test_force_release_semantics(fresh_db: str) -> None:
    """force 无视版本号推送，安装确认后停止；rearm 重新生效；常规发布不受确认影响。"""
    _publish("0.2.5", 7, force=True, target="dev-loop")

    rel = repository.latest_for("dev-loop", 7)
    assert rel is not None
    assert rel["force"] is True

    repository.mark_installed_upto("dev-loop", 7)
    assert repository.latest_for("dev-loop", 7) is None

    assert repository.rearm("dev-loop", "0.2.5") is True
    rel = repository.latest_for("dev-loop", 7)
    assert rel is not None
    assert rel["version"] == "0.2.5"

    _publish("0.3.0", 8)
    rel = repository.latest_for("dev-loop", 7)
    assert rel is not None
    assert rel["version"] == "0.3.0"


def test_force_higher_version_wins_over_normal(fresh_db: str) -> None:
    _publish("0.2.0", 2)
    _publish("0.2.0", 2, force=True, target="dev-1")

    rel = repository.latest_for("dev-1", 1)
    assert rel is not None
    assert rel["force"] is True


def test_mark_installed_upto_marks_all_lower_versions(fresh_db: str) -> None:
    _publish("0.1.0", 1)
    _publish("0.2.0", 2)

    repository.mark_installed_upto("dev-x", 2)
    assert repository.is_installed("dev-x", "0.1.0")
    assert repository.is_installed("dev-x", "0.2.0")
    assert not repository.is_installed("dev-y", "0.2.0")


def test_delete_release(fresh_db: str) -> None:
    _publish("0.2.0", 2)

    assert repository.delete_release("0.2.0") is True
    assert repository.delete_release("0.2.0") is False
    assert repository.list_releases() == []


# ── 服务层：路径安全与保留裁剪 ──


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
    """版本号参与文件路径拼接，非法形状在 service 层也被拒绝（目录穿越防线）。"""
    with pytest.raises(service.InvalidVersionError):
        service.release_path(malicious)


def test_release_path_accepts_semver() -> None:
    path = service.release_path("0.3.1")

    assert path.name == "0.3.1.apk"


def test_store_release_writes_file_and_returns_sha256(ota_dir: Path) -> None:
    sha = service.store_release("0.1.0", b"apk-bytes")

    assert len(sha) == 64
    assert (ota_dir / "0.1.0.apk").read_bytes() == b"apk-bytes"


def test_prune_keeps_recent_and_sweeps_orphans(ota_dir: Path) -> None:
    _publish("0.1.0", 1)
    _publish("0.1.1", 2)
    _publish("0.1.2", 3)
    (ota_dir / "0.0.9.apk").write_bytes(b"orphan")

    removed = service.prune_releases(keep=2)

    assert "0.1.0" in removed
    assert "0.0.9" in removed
    assert not (ota_dir / "0.1.0.apk").exists()
    assert not (ota_dir / "0.0.9.apk").exists()
    assert (ota_dir / "0.1.1.apk").exists()
    assert [rel["version"] for rel in repository.list_releases()] == ["0.1.2", "0.1.1"]


def test_prune_with_keep_larger_than_count_is_noop(ota_dir: Path) -> None:
    _publish("0.1.0", 1)

    assert service.prune_releases(keep=5) == []
    assert (ota_dir / "0.1.0.apk").exists()


# ── API 层：manifest、下载、上传、rearm、下架 ──


def test_manifest_reports_up_to_date_without_release(client: TestClient) -> None:
    r = client.get("/ota/manifest", params={"device_id": "dev-ota-1", "version_code": 6})

    assert r.json() == {"up_to_date": True}


def test_manifest_reports_update_with_frozen_url_and_size(client: TestClient, ota_dir: Path) -> None:
    """manifest 是设备硬编码契约，字段集逐项断言：设备端在发起下载前就读 sha256，
    缺任一字段都会让升级在下载前静默中止（2026-10-02 实际发生，设备卡在旧版）。
    """
    _publish("0.2.5", 7, target="dev-ota-1")

    r = client.get("/ota/manifest", params={"device_id": "dev-ota-1", "version_code": 6})

    body: dict[str, Any] = r.json()
    assert body["up_to_date"] is False
    assert body["version"] == "0.2.5"
    assert body["version_code"] == 7
    assert body["url"] == "/api/v1/ota/releases/0.2.5/download"
    assert body["sha256"] == "0" * 64
    assert body["size"] == len(b"apk-of-0.2.5")
    assert body["mandatory"] is False
    assert body["force"] is False


def test_manifest_requires_query_params(client: TestClient) -> None:
    """device_id 与 version_code 是必填 query：缺了当场 422。"""
    assert client.get("/ota/manifest").status_code == 422


def test_manifest_without_release_file_reports_up_to_date(client: TestClient, ota_dir: Path) -> None:
    """库里有记录、文件却不在：当作没有更新，不给设备一个下不动的地址。"""
    repository.insert_release("1.2.3", 8, "b" * 64, mandatory=False, force=False, target="dev-ota-2")

    r = client.get("/ota/manifest", params={"device_id": "dev-ota-2", "version_code": 6})

    assert r.json() == {"up_to_date": True}


def test_download_serves_apk_and_validates_version(client: TestClient, ota_dir: Path) -> None:
    _publish("0.2.5", 7)

    ok = client.get("/api/v1/ota/releases/0.2.5/download")
    assert ok.status_code == 200
    assert ok.content == b"apk-of-0.2.5"

    assert client.get("/api/v1/ota/releases/9.9.9/download").status_code == 404  # 格式合法但未发布
    assert client.get("/api/v1/ota/releases/no-such/download").status_code == 422  # 格式非法，HTTP 边界拦截


def test_upload_publishes_and_notifies_target_devices(
    client: TestClient,
    admin_headers: dict[str, str],
    ota_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上传登记发布并向在线目标设备即时推送；离线设备不补推。"""
    from app.core.connections import manager

    monkeypatch.setattr(
        manager, "_connections", {"dev-target-1": helpers.FakeGatewayWS(), "dev-other-9": helpers.FakeGatewayWS()}
    )

    r = client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "0.2.5", "version_code": 7, "target": "dev-target-1"},
        files={"file": ("ntu-shower-0.2.5.apk", io.BytesIO(b"PK"), "application/octet-stream")},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["notified_devices"] == ["dev-target-1"]
    assert len(body["sha256"]) == 64
    manifest = client.get("/ota/manifest", params={"device_id": "dev-target-1", "version_code": 6}).json()
    assert manifest["up_to_date"] is False


def test_upload_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    assert client.post("/api/v1/ota/releases").status_code == 401
    assert client.post("/api/v1/ota/releases", headers=ops_headers).status_code == 403


def test_upload_rejects_oversize(
    client: TestClient,
    admin_headers: dict[str, str],
    ota_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module.settings, "max_ota_bytes", 8)

    r = client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "9.9.9", "version_code": 99},
        files={"file": ("big.apk", b"x" * 9)},
    )

    assert r.status_code == 413
    assert r.json()["type"].endswith("/problems/release-too-large")


def test_upload_rejects_illegal_version_in_form(client: TestClient, admin_headers: dict[str, str]) -> None:
    """版本号在 HTTP 边界即按形状校验：不合法的进不了存储层。"""
    r = client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "no-such", "version_code": 1},
        files={"file": ("x.apk", b"PK")},
    )

    assert r.status_code == 422


def test_delete_release_removes_row_and_file(client: TestClient, admin_headers: dict[str, str], ota_dir: Path) -> None:
    _publish("0.1.0", 1)

    r = client.delete("/api/v1/ota/releases/0.1.0", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert not (ota_dir / "0.1.0.apk").exists()
    assert client.delete("/api/v1/ota/releases/0.1.0", headers=admin_headers).json()["deleted"] is False


def test_rearm_endpoint_clears_install_confirmation(client: TestClient, admin_headers: dict[str, str]) -> None:
    """有安装确认时 rearm 清掉它（返回 True），没有时幂等返回 False。"""
    _publish("0.3.1", 8)
    repository.mark_installed_upto("dev-rearm-1", 8)

    armed = client.post("/api/v1/ota/releases/0.3.1/rearm/dev-rearm-1", headers=admin_headers)
    assert armed.status_code == 200
    assert armed.json()["rearmed"] is True

    again = client.post("/api/v1/ota/releases/0.3.1/rearm/dev-rearm-1", headers=admin_headers)
    assert again.json()["rearmed"] is False


def test_release_list_is_public(client: TestClient, ota_dir: Path) -> None:
    """/api/v1/ota/releases 面向设备轮询，无需身份。"""
    _publish("0.2.0", 2)

    releases = client.get("/api/v1/ota/releases").json()

    assert any(rel["version"] == "0.2.0" for rel in releases)
