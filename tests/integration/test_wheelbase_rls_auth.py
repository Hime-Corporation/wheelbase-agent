"""Real local Supabase RLS matrix for Wheelbase agent credentials.

The backend integration orchestrator owns fixture creation and exports one
mode-0600 manifest path through ``WHEELBASE_RLS_TEST_MATRIX_FILE``. This test
will never accept a remote Supabase URL or print credential material.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# The repository root also contains the SDK project directory, which Python
# otherwise sees as an empty namespace package when the editable finder is not
# active for this integration path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "wheelbase_sdk"))

from plugins.wheelbase.wheelbase_core.tools.inventory_search import inventory_search
from tui_gateway.wheelbase_identity import WheelbaseIdentity, write_credential_file
from wheelbase_sdk import WheelbaseClient
from wheelbase_sdk import runtime as wheelbase_runtime


_MATRIX_ENV = "WHEELBASE_RLS_TEST_MATRIX_FILE"
_CASE_FIELDS = frozenset(
    {
        "name",
        "user_id",
        "tenant_id",
        "access_token",
        "credential_revision",
        "credential_expires_at",
        "expected_inventory_car_ids",
        "forbidden_inventory_car_ids",
    }
)


@dataclass(frozen=True)
class _Case:
    name: str
    user_id: str
    tenant_id: str
    access_token: str = field(repr=False)
    credential_revision: int
    credential_expires_at: int
    expected_inventory_car_ids: frozenset[str]
    forbidden_inventory_car_ids: frozenset[str]


@dataclass(frozen=True)
class _Matrix:
    supabase_url: str
    supabase_anon_key: str = field(repr=False)
    cases: tuple[_Case, ...]


def _require_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID string") from exc
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must use canonical UUID form")
    return value


def _require_uuid_set(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a nonempty list")
    parsed = frozenset(_require_uuid(item, label) for item in value)
    if len(parsed) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return parsed


def _require_loopback_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("supabase_url must be nonempty")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("supabase_url must be an HTTP loopback URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("supabase_url must not contain credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname == "localhost"
    if not loopback:
        raise ValueError("supabase_url must resolve explicitly to loopback")
    if parsed.path not in {"", "/"}:
        raise ValueError("supabase_url must not contain a path")
    return value.strip().rstrip("/")


def _load_matrix(path: Path) -> _Matrix:
    if not path.is_absolute():
        raise ValueError("matrix path must be absolute")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("matrix file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("matrix path must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("matrix file mode must be exactly 0600")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("matrix file must contain valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "supabase_url",
        "supabase_anon_key",
        "cases",
    }:
        raise ValueError("matrix must contain only the version, Supabase, and cases fields")
    if raw.get("version") != 1:
        raise ValueError("matrix version must be 1")
    url = _require_loopback_url(raw.get("supabase_url"))
    anon_key = raw.get("supabase_anon_key")
    if not isinstance(anon_key, str) or not anon_key.strip():
        raise ValueError("supabase_anon_key must be nonempty")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) < 2:
        raise ValueError("matrix requires at least two cases")

    cases: list[_Case] = []
    names: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        label = f"case {index}"
        if not isinstance(raw_case, dict) or set(raw_case) != _CASE_FIELDS:
            raise ValueError(f"{label} has an invalid field set")
        name = raw_case.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"{label} name must be nonempty and unique")
        names.add(name)
        token = raw_case.get("access_token")
        revision = raw_case.get("credential_revision")
        expiry = raw_case.get("credential_expires_at")
        if not isinstance(token, str) or not token.strip():
            raise ValueError(f"{label} access token must be nonempty")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError(f"{label} credential revision must be positive")
        if (
            not isinstance(expiry, int)
            or isinstance(expiry, bool)
            or expiry <= int(time.time()) + 30
        ):
            raise ValueError(f"{label} credential expiry must exceed the SDK safety skew")
        expected = _require_uuid_set(
            raw_case.get("expected_inventory_car_ids"),
            f"{label} expected_inventory_car_ids",
        )
        forbidden = _require_uuid_set(
            raw_case.get("forbidden_inventory_car_ids"),
            f"{label} forbidden_inventory_car_ids",
        )
        if expected & forbidden:
            raise ValueError(f"{label} expected and forbidden inventory must be disjoint")
        cases.append(
            _Case(
                name=name,
                user_id=_require_uuid(raw_case.get("user_id"), f"{label} user_id"),
                tenant_id=_require_uuid(raw_case.get("tenant_id"), f"{label} tenant_id"),
                access_token=token.strip(),
                credential_revision=revision,
                credential_expires_at=expiry,
                expected_inventory_car_ids=expected,
                forbidden_inventory_car_ids=forbidden,
            )
        )
    return _Matrix(url, anon_key.strip(), tuple(cases))


def _example_manifest(url: str = "http://127.0.0.1:54321") -> dict:
    expiry = int(time.time()) + 600
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    return {
        "version": 1,
        "supabase_url": url,
        "supabase_anon_key": "local-anon-key",
        "cases": [
            {
                "name": f"tenant-{index}",
                "user_id": str(uuid.uuid4()),
                "tenant_id": str(uuid.uuid4()),
                "access_token": f"local-test-token-{index}",
                "credential_revision": 1,
                "credential_expires_at": expiry,
                "expected_inventory_car_ids": [ids[index]],
                "forbidden_inventory_car_ids": [ids[1 - index]],
            }
            for index in range(2)
        ],
    }


def _write_example(path: Path, manifest: dict, *, mode: int = 0o600) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")
    path.chmod(mode)


def test_rls_matrix_rejects_non_loopback_url(tmp_path):
    path = tmp_path / "matrix.json"
    _write_example(path, _example_manifest("https://example.supabase.co"))
    with pytest.raises(ValueError, match="loopback"):
        _load_matrix(path)


def test_rls_matrix_rejects_insecure_permissions(tmp_path):
    path = tmp_path / "matrix.json"
    _write_example(path, _example_manifest(), mode=0o644)
    with pytest.raises(ValueError, match="0600"):
        _load_matrix(path)


def test_wheelbase_rls_auth(tmp_path, monkeypatch):
    raw_path = os.environ.get(_MATRIX_ENV, "").strip()
    if not raw_path:
        pytest.skip(f"{_MATRIX_ENV} absent; local Supabase RLS orchestrator not running")
    matrix = _load_matrix(Path(raw_path))
    monkeypatch.setenv("SUPABASE_URL", matrix.supabase_url)
    monkeypatch.setenv("SUPABASE_ANON_KEY", matrix.supabase_anon_key)

    for case in matrix.cases:
        jti_hash = hashlib.sha256(
            f"wheelbase-rls-test\0{case.name}\0{case.user_id}".encode()
        ).hexdigest()
        identity = WheelbaseIdentity(
            user_id=case.user_id,
            tenant_id=case.tenant_id,
            jwt=case.access_token,
            client="mobile",
            session_jti_hash=jti_hash,
            credential_revision=case.credential_revision,
            credential_expires_at=case.credential_expires_at,
            credential_source="agent_gateway_identity",
        )
        credential_path = write_credential_file(tmp_path, identity)
        task_id = f"rls-{hashlib.sha256(case.name.encode()).hexdigest()[:12]}"
        context_token = wheelbase_runtime.set_task_identity(
            task_id,
            {
                "user_id": case.user_id,
                "tenant_id": case.tenant_id,
                "credential_path": str(credential_path),
                "session_jti_hash": jti_hash,
                "credential_revision": case.credential_revision,
                "credential_expires_at": case.credential_expires_at,
                "credential_source": "agent_gateway_identity",
            },
        )
        try:
            with WheelbaseClient() as client:
                direct_rows = client.postgrest_get(
                    "inventory_car", {"select": "id", "order": "id.asc"}
                )
            direct_ids = {str(row.get("id")) for row in direct_rows}
            if not case.expected_inventory_car_ids <= direct_ids:
                pytest.fail(f"{case.name}: own-tenant inventory was not visible")
            if case.forbidden_inventory_car_ids & direct_ids:
                pytest.fail(f"{case.name}: cross-tenant inventory was visible")

            tool_result = json.loads(inventory_search({"limit": 200}))
            if "error" in tool_result:
                pytest.fail(f"{case.name}: inventory_search returned an error")
            tool_ids = {str(row.get("id")) for row in tool_result.get("results", [])}
            if not case.expected_inventory_car_ids <= tool_ids:
                pytest.fail(f"{case.name}: plugin omitted own-tenant inventory")
            if case.forbidden_inventory_car_ids & tool_ids:
                pytest.fail(f"{case.name}: plugin exposed cross-tenant inventory")
        finally:
            wheelbase_runtime.reset_identity(context_token)
            wheelbase_runtime.clear_task(task_id)
