"""Tests for tui_gateway.tenant_migration — flat -> tenant-keyed profile move."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tui_gateway import tenant_migration as tm


def _mk_profile(root: Path, uid: str) -> Path:
    d = root / "profiles" / f"wb-{uid}"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("profile: true\n")
    return d


class _FakeResponse:
    def __init__(self, rows: list[dict[str, Any]], status_code: int = 200):
        self._rows = rows
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> list[dict[str, Any]]:
        return self._rows


def test_marker_short_circuit_returns_all_zero(tmp_path: Path) -> None:
    marker = tmp_path / "tenants" / ".migration-completed"
    marker.parent.mkdir(parents=True)
    marker.write_text("")
    # Legacy profiles still present, but marker wins — nothing touched.
    _mk_profile(tmp_path, "user1")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report == tm.MigrationReport()
    mock_httpx.get.assert_not_called()
    assert (tmp_path / "profiles" / "wb-user1").is_dir()


def test_fresh_install_writes_marker_and_returns_zero(tmp_path: Path) -> None:
    report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report == tm.MigrationReport()
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_fresh_install_no_profiles_dir(tmp_path: Path) -> None:
    # profiles/ doesn't even exist.
    report = tm.run_tenant_migration(tmp_path, "", "")
    assert report == tm.MigrationReport()
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_missing_creds_with_legacy_profiles_no_moves_no_marker(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")

    report = tm.run_tenant_migration(tmp_path, "", "")

    assert report.migrated == 0
    assert report.orphaned == 0
    assert len(report.errors) == 1
    assert "supabase_url/supabase_key" in report.errors[0]
    assert not (tmp_path / "tenants" / ".migration-completed").exists()
    assert (tmp_path / "profiles" / "wb-user1").is_dir()


def test_happy_path_multi_user_multi_tenant(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")
    _mk_profile(tmp_path, "user2")
    _mk_profile(tmp_path, "user3")

    rows = [
        {"id": "user1", "tenant_id": "tenantA"},
        {"id": "user2", "tenant_id": "tenantB"},
        {"id": "user3", "tenant_id": "tenantA"},
    ]

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse(rows)
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.migrated == 3
    assert report.orphaned == 0
    assert report.skipped == 0
    assert (tmp_path / "tenants" / "tenantA" / "profiles" / "wb-user1").is_dir()
    assert (tmp_path / "tenants" / "tenantB" / "profiles" / "wb-user2").is_dir()
    assert (tmp_path / "tenants" / "tenantA" / "profiles" / "wb-user3").is_dir()
    assert not (tmp_path / "profiles" / "wb-user1").exists()
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_orphan_when_uid_not_in_supabase(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "ghost")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse([])  # no matching row
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.migrated == 0
    assert report.orphaned == 1
    assert (tmp_path / "_orphaned" / "wb-ghost").is_dir()
    assert not (tmp_path / "profiles" / "wb-ghost").exists()
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_orphan_name_collision_gets_numeric_suffix(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "ghost")
    # Pre-existing orphan dir with the same target name.
    existing = tmp_path / "_orphaned" / "wb-ghost"
    existing.mkdir(parents=True)
    (existing / "marker.txt").write_text("already here")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse([])
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.orphaned == 1
    assert (tmp_path / "_orphaned" / "wb-ghost").is_dir()
    assert (tmp_path / "_orphaned" / "wb-ghost" / "marker.txt").exists()
    assert (tmp_path / "_orphaned" / "wb-ghost-2").is_dir()


def test_invalid_dir_name_is_skipped_not_touched(tmp_path: Path) -> None:
    bad = tmp_path / "profiles" / "wb-../../etc"
    bad.mkdir(parents=True)
    good = _mk_profile(tmp_path, "user1")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse([{"id": "user1", "tenant_id": "tenantA"}])
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.skipped == 1
    assert report.migrated == 1
    assert bad.is_dir()  # left alone
    assert not good.exists()


def test_partial_network_failure_keeps_successful_moves_no_marker(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")

    # First pass: user1 resolves and migrates cleanly; marker gets written.
    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse([{"id": "user1", "tenant_id": "tenantA"}])
        first_report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert first_report.migrated == 1
    assert (tmp_path / "tenants" / ".migration-completed").exists()
    assert (tmp_path / "tenants" / "tenantA" / "profiles" / "wb-user1").is_dir()

    # A second legacy profile shows up later (e.g. restored from backup) and
    # the marker is cleared to force a fresh boot pass, but Supabase is
    # unreachable this time.
    (tmp_path / "tenants" / ".migration-completed").unlink()
    _mk_profile(tmp_path, "user2")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.side_effect = RuntimeError("connection reset")
        second_report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert second_report.migrated == 0
    assert len(second_report.errors) == 1
    assert not (tmp_path / "tenants" / ".migration-completed").exists()
    # user1's earlier successful move is untouched by the failed pass.
    assert (tmp_path / "tenants" / "tenantA" / "profiles" / "wb-user1").is_dir()
    # user2 never moved because the lookup failed.
    assert (tmp_path / "profiles" / "wb-user2").is_dir()
    # Retried once (2 attempts) before raising.
    assert mock_httpx.get.call_count == 2


def test_idempotent_rerun_after_partial_leaves_moved_dirs_untouched(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")
    _mk_profile(tmp_path, "user2")

    rows = [
        {"id": "user1", "tenant_id": "tenantA"},
        {"id": "user2", "tenant_id": "tenantB"},
    ]
    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse(rows)
        first_report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")
    assert first_report.migrated == 2

    # Re-run: marker present now, so this should be a pure no-op.
    with mock.patch.object(tm, "httpx") as mock_httpx:
        second_report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert second_report == tm.MigrationReport()
    mock_httpx.get.assert_not_called()
    assert (tmp_path / "tenants" / "tenantA" / "profiles" / "wb-user1").is_dir()
    assert (tmp_path / "tenants" / "tenantB" / "profiles" / "wb-user2").is_dir()


def test_retry_then_success_on_transient_failure(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")

    ok_response = _FakeResponse([{"id": "user1", "tenant_id": "tenantA"}])
    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.side_effect = [RuntimeError("transient"), ok_response]
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.migrated == 1
    assert mock_httpx.get.call_count == 2
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_batched_query_uses_in_filter_shape(tmp_path: Path) -> None:
    uids = [f"user{i}" for i in range(75)]  # forces 2 chunks at _CHUNK_SIZE=50
    for uid in uids:
        _mk_profile(tmp_path, uid)

    captured_params: list[dict[str, Any]] = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        captured_params.append(params)
        return _FakeResponse([])

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.side_effect = _fake_get
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert len(captured_params) == 2
    for params in captured_params:
        assert params["select"] == "id,tenant_id"
        assert params["id"].startswith("in.(")
        assert params["id"].endswith(")")

    chunk_sizes = [p["id"].count(",") + 1 for p in captured_params]
    assert sorted(chunk_sizes) == [25, 50]

    covered_uids = set()
    for params in captured_params:
        inner = params["id"][len("in.(") : -1]
        covered_uids.update(inner.split(","))
    assert covered_uids == set(uids)

    assert report.orphaned == 75


def test_auth_json_noted_but_not_moved(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text('{"token": "secret"}')

    report = tm.run_tenant_migration(tmp_path, "", "")

    assert (tmp_path / "auth.json").exists()
    assert any("auth.json" in e for e in report.errors)
    assert (tmp_path / "tenants" / ".migration-completed").exists()


def test_invalid_tenant_id_from_supabase_is_treated_as_orphan(tmp_path: Path) -> None:
    _mk_profile(tmp_path, "user1")

    with mock.patch.object(tm, "httpx") as mock_httpx:
        mock_httpx.get.return_value = _FakeResponse(
            [{"id": "user1", "tenant_id": "../../etc"}]
        )
        report = tm.run_tenant_migration(tmp_path, "https://supabase.example", "key")

    assert report.migrated == 0
    assert report.orphaned == 1
    assert (tmp_path / "_orphaned" / "wb-user1").is_dir()
