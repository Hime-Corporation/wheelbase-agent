"""Real local Supabase RLS matrix for Wheelbase agent credentials.

The backend integration orchestrator owns fixture creation and exports one
mode-0600 manifest path through ``WHEELBASE_RLS_TEST_MATRIX_FILE``. This test
will never accept a remote Supabase URL or print credential material.
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import http.server
import ipaddress
import json
import os
import socket
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import httpx
from starlette.testclient import TestClient

# The repository root also contains the SDK project directory, which Python
# otherwise sees as an empty namespace package when the editable finder is not
# active for this integration path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "wheelbase_sdk"))

from plugins.wheelbase.wheelbase_core.tools.inventory_search import inventory_search
from tui_gateway.wheelbase_identity import WheelbaseIdentity, write_credential_file
from tui_gateway.profile_router import ChildManager, build_app
from wheelbase_sdk import WheelbaseClient
from wheelbase_sdk import runtime as wheelbase_runtime
from wheelbase_sdk.errors import WheelbaseAuthError


_MATRIX_ENV = "WHEELBASE_RLS_TEST_MATRIX_FILE"
_CASE_FIELDS = frozenset(
    {
        "name",
        "user_id",
        "tenant_id",
        "dealer_id",
        "client",
        "device_id",
        "user_status",
        "access_token",
        "credential_revision",
        "credential_expires_at",
        "expected_inventory_car_ids",
        "forbidden_inventory_car_ids",
    }
)
_CASE_NAMES = frozenset(
    {
        "tenant-a-user-a-d1",
        "tenant-a-user-a-d2",
        "tenant-a-user-a-mobile",
        "tenant-a-user-b-d1",
        "tenant-b-user-c-d1",
        "tenant-b-user-a-multi",
        "tenant-a-user-c-suspended",
    }
)
_SCENARIO_FIELDS = {
    "replica_lifecycle": frozenset({"mint_base", "connect_base", "restart_order"}),
    "independent_refresh": frozenset({"case_names"}),
    "multi_tenant": frozenset({"case_name", "alternate_tenant_id", "alternate_dealer_id"}),
    "cross_dealer": frozenset({"case_name", "target_dealer_id", "expected_status"}),
    "suspended": frozenset({"case_name", "expected_status"}),
    "wrong_user_refresh": frozenset({"session_case", "bearer_case", "expected_status"}),
    "revoked_replay": frozenset({"case_name", "expected_close_code"}),
    "expiry": frozenset({"case_name", "expected_close_code"}),
    "store_outage": frozenset({"case_name", "expected_close_code"}),
    "unsafe_retry": frozenset({"case_name", "max_retries"}),
}


@dataclass(frozen=True)
class _Case:
    name: str
    user_id: str
    tenant_id: str
    dealer_id: str
    client: str
    device_id: str | None
    user_status: str
    access_token: str = field(repr=False)
    credential_revision: int
    credential_expires_at: int
    expected_inventory_car_ids: frozenset[str]
    forbidden_inventory_car_ids: frozenset[str]


@dataclass(frozen=True)
class _Matrix:
    supabase_url: str
    supabase_anon_key: str = field(repr=False)
    api_replicas: dict[str, str]
    cases: tuple[_Case, ...]
    scenarios: dict[str, dict[str, object]]


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


def _require_uuid_set(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> frozenset[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a nonempty list"
        raise ValueError(f"{label} must be {qualifier}")
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
        "api_replicas",
        "cases",
        "scenarios",
    }:
        raise ValueError("matrix has an invalid top-level field set")
    if raw.get("version") != 2:
        raise ValueError("matrix version must be 2")
    url = _require_loopback_url(raw.get("supabase_url"))
    anon_key = raw.get("supabase_anon_key")
    if not isinstance(anon_key, str) or not anon_key.strip():
        raise ValueError("supabase_anon_key must be nonempty")
    raw_replicas = raw.get("api_replicas")
    if not isinstance(raw_replicas, dict) or set(raw_replicas) != {"a", "b"}:
        raise ValueError("api_replicas must contain only a and b")
    replicas = {
        name: _require_loopback_url(value)
        for name, value in raw_replicas.items()
    }
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(_CASE_NAMES):
        raise ValueError("matrix requires the seven named cases")

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
        client = raw_case.get("client")
        device_id = raw_case.get("device_id")
        if client not in {"desktop", "mobile"}:
            raise ValueError(f"{label} client must be desktop or mobile")
        if client == "desktop":
            device_id = _require_uuid(device_id, f"{label} device_id")
        elif device_id is not None:
            raise ValueError(f"{label} mobile device_id must be null")
        user_status = raw_case.get("user_status")
        if user_status not in {"active", "pending", "revoked"}:
            raise ValueError(
                f"{label} user_status must use the tenant_users status vocabulary"
            )
        expected = _require_uuid_set(
            raw_case.get("expected_inventory_car_ids"),
            f"{label} expected_inventory_car_ids",
            allow_empty=True,
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
                dealer_id=_require_uuid(raw_case.get("dealer_id"), f"{label} dealer_id"),
                client=client,
                device_id=device_id,
                user_status=user_status,
                access_token=token.strip(),
                credential_revision=revision,
                credential_expires_at=expiry,
                expected_inventory_car_ids=expected,
                forbidden_inventory_car_ids=forbidden,
            )
        )
    if names != _CASE_NAMES:
        raise ValueError("matrix case names do not match the required contract")

    raw_scenarios = raw.get("scenarios")
    if not isinstance(raw_scenarios, dict) or set(raw_scenarios) != set(_SCENARIO_FIELDS):
        raise ValueError("matrix scenarios do not match the required contract")
    scenarios: dict[str, dict[str, object]] = {}
    for name, required_fields in _SCENARIO_FIELDS.items():
        value = raw_scenarios.get(name)
        if not isinstance(value, dict) or set(value) != required_fields:
            raise ValueError(f"scenario {name} has an invalid field set")
        scenarios[name] = dict(value)

    def require_case_ref(value: object, label: str) -> str:
        if not isinstance(value, str) or value not in names:
            raise ValueError(f"{label} must reference a named case")
        return value

    replica = scenarios["replica_lifecycle"]
    for field_name in ("mint_base", "connect_base"):
        if replica[field_name] not in replicas:
            raise ValueError(f"replica_lifecycle {field_name} must reference a replica")
    restart_order = replica["restart_order"]
    if not isinstance(restart_order, list) or len(restart_order) != 2 or set(restart_order) != set(replicas):
        raise ValueError("replica_lifecycle restart_order must contain a and b once")

    refresh_names = scenarios["independent_refresh"]["case_names"]
    if not isinstance(refresh_names, list) or len(refresh_names) < 2 or len(set(refresh_names)) != len(refresh_names):
        raise ValueError("independent_refresh case_names must be unique")
    for case_name in refresh_names:
        require_case_ref(case_name, "independent_refresh case_names")

    for scenario_name in (
        "multi_tenant",
        "cross_dealer",
        "suspended",
        "revoked_replay",
        "expiry",
        "store_outage",
        "unsafe_retry",
    ):
        require_case_ref(scenarios[scenario_name]["case_name"], f"{scenario_name} case_name")
    wrong_user = scenarios["wrong_user_refresh"]
    require_case_ref(wrong_user["session_case"], "wrong_user_refresh session_case")
    require_case_ref(wrong_user["bearer_case"], "wrong_user_refresh bearer_case")

    multi = scenarios["multi_tenant"]
    _require_uuid(multi["alternate_tenant_id"], "multi_tenant alternate_tenant_id")
    _require_uuid(multi["alternate_dealer_id"], "multi_tenant alternate_dealer_id")
    _require_uuid(scenarios["cross_dealer"]["target_dealer_id"], "cross_dealer target_dealer_id")

    for scenario_name in ("cross_dealer", "suspended", "wrong_user_refresh"):
        status = scenarios[scenario_name]["expected_status"]
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            raise ValueError(f"{scenario_name} expected_status must be an HTTP error status")
    for scenario_name in ("revoked_replay", "expiry", "store_outage"):
        close_code = scenarios[scenario_name]["expected_close_code"]
        if not isinstance(close_code, int) or isinstance(close_code, bool) or not 1000 <= close_code <= 4999:
            raise ValueError(f"{scenario_name} expected_close_code must be a WebSocket close code")
    max_retries = scenarios["unsafe_retry"]["max_retries"]
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("unsafe_retry max_retries must be nonnegative")

    return _Matrix(url, anon_key.strip(), replicas, tuple(cases), scenarios)


def _example_manifest(url: str = "http://127.0.0.1:54321") -> dict:
    expiry = int(time.time()) + 600
    users = {name: str(uuid.uuid4()) for name in ("a", "b", "c")}
    tenants = {name: str(uuid.uuid4()) for name in ("a", "b")}
    dealers = {name: str(uuid.uuid4()) for name in ("a", "b", "c")}
    cars = {name: str(uuid.uuid4()) for name in ("a", "b", "c")}
    devices = {name: str(uuid.uuid4()) for name in ("d1", "d2", "b", "c")}

    def case(
        name: str,
        *,
        user: str,
        tenant: str,
        dealer: str,
        client: str = "desktop",
        device: str | None = "d1",
        status: str = "active",
        expected: tuple[str, ...] = ("a",),
    ) -> dict:
        expected_ids = [cars[item] for item in expected]
        return {
            "name": name,
            "user_id": users[user],
            "tenant_id": tenants[tenant],
            "dealer_id": dealers[dealer],
            "client": client,
            "device_id": devices[device] if device is not None else None,
            "user_status": status,
            "access_token": f"local-test-token-{name}",
            "credential_revision": 1,
            "credential_expires_at": expiry,
            "expected_inventory_car_ids": expected_ids,
            "forbidden_inventory_car_ids": [
                car_id for car_id in cars.values() if car_id not in expected_ids
            ],
        }

    return {
        "version": 2,
        "supabase_url": url,
        "supabase_anon_key": "local-anon-key",
        "api_replicas": {
            "a": "http://127.0.0.1:18081",
            "b": "http://127.0.0.1:18082",
        },
        "cases": [
            case("tenant-a-user-a-d1", user="a", tenant="a", dealer="a"),
            case("tenant-a-user-a-d2", user="a", tenant="a", dealer="a", device="d2"),
            case("tenant-a-user-a-mobile", user="a", tenant="a", dealer="a", client="mobile", device=None),
            case("tenant-a-user-b-d1", user="b", tenant="a", dealer="c", device="b", expected=("c",)),
            case("tenant-b-user-c-d1", user="c", tenant="b", dealer="b", device="c", expected=("b",)),
            case("tenant-b-user-a-multi", user="a", tenant="b", dealer="b", device="d1", expected=("b",)),
            # The scenario name is frozen as "suspended" but the real
            # tenant_users inactive state is called "revoked".
            case("tenant-a-user-c-suspended", user="c", tenant="a", dealer="a", device="c", status="revoked", expected=()),
        ],
        "scenarios": {
            "replica_lifecycle": {"mint_base": "a", "connect_base": "b", "restart_order": ["a", "b"]},
            "independent_refresh": {"case_names": ["tenant-a-user-a-d1", "tenant-a-user-a-d2"]},
            "multi_tenant": {"case_name": "tenant-b-user-a-multi", "alternate_tenant_id": tenants["a"], "alternate_dealer_id": dealers["a"]},
            "cross_dealer": {"case_name": "tenant-a-user-b-d1", "target_dealer_id": dealers["a"], "expected_status": 403},
            "suspended": {"case_name": "tenant-a-user-c-suspended", "expected_status": 403},
            "wrong_user_refresh": {"session_case": "tenant-a-user-a-d1", "bearer_case": "tenant-a-user-b-d1", "expected_status": 403},
            "revoked_replay": {"case_name": "tenant-a-user-a-d1", "expected_close_code": 4003},
            "expiry": {"case_name": "tenant-a-user-a-mobile", "expected_close_code": 4003},
            "store_outage": {"case_name": "tenant-b-user-c-d1", "expected_close_code": 1011},
            "unsafe_retry": {"case_name": "tenant-a-user-a-d1", "max_retries": 0},
        },
    }


def _write_example(path: Path, manifest: dict, *, mode: int = 0o600) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")
    path.chmod(mode)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed_envelope(
    case: _Case,
    key: bytes,
    *,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    header = {"alg": "HS256", "typ": "JWT", "kid": "integration"}
    bundle = {
        "user_id": case.user_id,
        "tenant_id": case.tenant_id,
        "dealership_id": case.dealer_id,
        "client": case.client,
        "device_id": case.device_id or "",
        "session_jti_hash": hashlib.sha256(
            f"gateway-fixture\0{case.name}".encode()
        ).hexdigest(),
        "credential_revision": case.credential_revision,
        "credential_expires_at": case.credential_expires_at,
        "access_token": case.access_token,
    }
    payload = {
        "iss": "wheelbase-api",
        "aud": "wheelbase-agent-gateway",
        "kind": "agent_gateway_identity",
        "ver": 2,
        "iat": issued_at,
        "exp": issued_at + 20,
        "nonce": nonce or str(uuid.uuid4()),
        "bundle": bundle,
    }
    signing = ".".join(
        (
            _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
            _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
        )
    )
    signature = hmac.new(key, signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{_b64url(signature)}"


class _ChildGatewayProcess:
    def __init__(self, port: int, session_token: str, profile_home: str):
        self.requests: list[dict[str, object]] = []
        self._stopped = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler contract
                accepted = hmac.compare_digest(
                    self.headers.get("X-Hermes-Session-Token", ""),
                    session_token,
                )
                outer.requests.append(
                    {
                        "accepted": accepted,
                        "has_envelope": bool(
                            self.headers.get("X-Wheelbase-Identity-Envelope")
                        ),
                        "path": self.path,
                    }
                )
                body = json.dumps(
                    {
                        "ok": accepted,
                        "profile_fp": hashlib.sha256(profile_home.encode()).hexdigest()[:12],
                    }
                ).encode()
                self.send_response(200 if accepted else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def poll(self):
        return 0 if self._stopped else None

    def terminate(self):
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _RealGatewayFixture:
    def __init__(self, hermes_home: Path):
        self.processes: list[_ChildGatewayProcess] = []

        def spawn(_user_id: str, port: int, env: dict[str, str]):
            process = _ChildGatewayProcess(
                port,
                env["HERMES_DASHBOARD_SESSION_TOKEN"],
                env["HERMES_HOME"],
            )
            self.processes.append(process)
            return process

        self.manager = ChildManager(
            profiles_root=hermes_home,
            spawn=spawn,
            wait_ready=lambda _port, _token: None,
            seed_skills=lambda _path: None,
        )

        def allocate_port() -> int:
            with socket.socket() as candidate:
                candidate.bind(("127.0.0.1", 0))
                return int(candidate.getsockname()[1])

        self.manager._alloc_port = allocate_port
        self.client = TestClient(build_app(self.manager))

    def close(self):
        self.client.close()
        for process in self.processes:
            process.terminate()


class _StatusProbeServer:
    def __init__(self, status_code: int, on_request=None):
        self.requests = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _respond(self):
                outer.requests += 1
                if on_request is not None:
                    on_request(outer.requests)
                body = b"[]"
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PATCH = do_DELETE = _respond

            def log_message(self, *_args):
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


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


def test_rls_matrix_consumes_expanded_named_contract(tmp_path):
    path = tmp_path / "matrix.json"
    _write_example(path, _example_manifest())

    matrix = _load_matrix(path)

    assert {case.name for case in matrix.cases} == {
        "tenant-a-user-a-d1",
        "tenant-a-user-a-d2",
        "tenant-a-user-a-mobile",
        "tenant-a-user-b-d1",
        "tenant-b-user-c-d1",
        "tenant-b-user-a-multi",
        "tenant-a-user-c-suspended",
    }
    assert set(matrix.api_replicas) == {"a", "b"}
    assert set(matrix.scenarios) == {
        "replica_lifecycle",
        "independent_refresh",
        "multi_tenant",
        "cross_dealer",
        "suspended",
        "wrong_user_refresh",
        "revoked_replay",
        "expiry",
        "store_outage",
        "unsafe_retry",
    }


def test_real_gateway_routes_all_named_cases_to_isolated_profiles(
    tmp_path, monkeypatch, caplog
):
    manifest_path = tmp_path / "matrix.json"
    _write_example(manifest_path, _example_manifest())
    matrix = _load_matrix(manifest_path)
    key = b"g" * 32
    monkeypatch.setenv(
        "AGENT_GATEWAY_IDENTITY_KEYS",
        json.dumps({"integration": base64.b64encode(key).decode()}),
    )
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "router-fixture-token")
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    gateway = _RealGatewayFixture(hermes_home)
    try:
        for case in matrix.cases:
            response = gateway.client.get(
                "/api/status",
                headers={
                    "X-Hermes-Session-Token": "router-fixture-token",
                    "X-Wheelbase-Identity-Envelope": _signed_envelope(case, key),
                },
            )
            assert response.status_code == 200, case.name
            assert response.json()["ok"] is True

        expected_profiles = {(case.tenant_id, case.user_id) for case in matrix.cases}
        assert set(gateway.manager._children) == expected_profiles
        for (tenant_id, user_id), child in gateway.manager._children.items():
            assert child.profile_dir == (
                hermes_home / "tenants" / tenant_id / "profiles" / f"wb-{user_id}"
            )
            assert child.profile_dir.is_dir()
        requests = [request for process in gateway.processes for request in process.requests]
        assert len(requests) == len(matrix.cases)
        assert all(request["accepted"] is True for request in requests)
        assert all(request["has_envelope"] is True for request in requests)
        log_text = "\n".join(record.message for record in caplog.records)
        assert all(case.access_token not in log_text for case in matrix.cases)
    finally:
        gateway.close()


def test_real_gateway_rejects_swapped_expired_and_replayed_envelopes(
    tmp_path, monkeypatch
):
    manifest_path = tmp_path / "matrix.json"
    _write_example(manifest_path, _example_manifest())
    matrix = _load_matrix(manifest_path)
    case = matrix.cases[0]
    key = b"h" * 32
    monkeypatch.setenv(
        "AGENT_GATEWAY_IDENTITY_KEYS",
        json.dumps({"integration": base64.b64encode(key).decode()}),
    )
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "router-fixture-token")
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    gateway = _RealGatewayFixture(hermes_home)
    headers = {"X-Hermes-Session-Token": "router-fixture-token"}
    try:
        signed = _signed_envelope(case, key)
        encoded_header, encoded_payload, signature = signed.split(".")
        payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        )
        payload["bundle"]["tenant_id"] = str(uuid.uuid4())
        swapped = ".".join(
            (
                encoded_header,
                _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
                signature,
            )
        )
        assert gateway.client.get(
            "/api/status",
            headers={**headers, "X-Wheelbase-Identity-Envelope": swapped},
        ).status_code == 403

        expired = _signed_envelope(case, key, now=int(time.time()) - 60)
        assert gateway.client.get(
            "/api/status",
            headers={**headers, "X-Wheelbase-Identity-Envelope": expired},
        ).status_code == 403

        replayed = _signed_envelope(case, key)
        assert gateway.client.get(
            "/api/status",
            headers={**headers, "X-Wheelbase-Identity-Envelope": replayed},
        ).status_code == 200
        assert gateway.client.get(
            "/api/status",
            headers={**headers, "X-Wheelbase-Identity-Envelope": replayed},
        ).status_code == 403
    finally:
        gateway.close()


def test_real_http_unsafe_write_does_not_retry_after_credential_rotation(
    tmp_path, monkeypatch, caplog
):
    manifest_path = tmp_path / "matrix.json"
    _write_example(manifest_path, _example_manifest())
    matrix = _load_matrix(manifest_path)
    scenario = matrix.scenarios["unsafe_retry"]
    case = next(item for item in matrix.cases if item.name == scenario["case_name"])
    jti_hash = hashlib.sha256(f"unsafe-retry\0{case.name}".encode()).hexdigest()
    identity = WheelbaseIdentity(
        user_id=case.user_id,
        tenant_id=case.tenant_id,
        dealership_id=case.dealer_id,
        jwt=case.access_token,
        client=case.client,
        device_id=case.device_id or "",
        session_jti_hash=jti_hash,
        credential_revision=case.credential_revision,
        credential_expires_at=case.credential_expires_at,
        credential_source="agent_gateway_identity",
    )
    credential_path = write_credential_file(tmp_path, identity)

    def rotate_once(request_number: int) -> None:
        if request_number == 1:
            write_credential_file(
                tmp_path,
                WheelbaseIdentity(
                    **{
                        **identity.__dict__,
                        "jwt": "rotated-token-never-retried",
                        "credential_revision": identity.credential_revision + 1,
                    }
                ),
            )

    probe = _StatusProbeServer(401, rotate_once)
    runtime_token = wheelbase_runtime.set_task_identity(
        "unsafe-write",
        {
            **identity.__dict__,
            "credential_path": str(credential_path),
        },
    )
    monkeypatch.setenv("SUPABASE_URL", probe.origin)
    monkeypatch.setenv("SUPABASE_ANON_KEY", matrix.supabase_anon_key)
    try:
        with WheelbaseClient(timeout=2) as client:
            with pytest.raises(WheelbaseAuthError):
                client.postgrest_write("POST", "inventory_car", body={})
        assert probe.requests == 1
        assert probe.requests - 1 <= scenario["max_retries"]
        log_text = "\n".join(record.message for record in caplog.records)
        assert case.access_token not in log_text
        assert "rotated-token-never-retried" not in log_text
    finally:
        probe.close()
        wheelbase_runtime.reset_identity(runtime_token)
        wheelbase_runtime.clear_task("unsafe-write")


def test_real_http_store_outage_fails_without_hidden_retry(tmp_path, monkeypatch):
    manifest_path = tmp_path / "matrix.json"
    _write_example(manifest_path, _example_manifest())
    matrix = _load_matrix(manifest_path)
    scenario = matrix.scenarios["store_outage"]
    case = next(item for item in matrix.cases if item.name == scenario["case_name"])
    identity = WheelbaseIdentity(
        user_id=case.user_id,
        tenant_id=case.tenant_id,
        dealership_id=case.dealer_id,
        jwt=case.access_token,
        client=case.client,
        device_id=case.device_id or "",
        session_jti_hash=hashlib.sha256(f"store-outage\0{case.name}".encode()).hexdigest(),
        credential_revision=case.credential_revision,
        credential_expires_at=case.credential_expires_at,
        credential_source="agent_gateway_identity",
    )
    credential_path = write_credential_file(tmp_path, identity)
    probe = _StatusProbeServer(503)
    runtime_token = wheelbase_runtime.set_task_identity(
        "store-outage",
        {**identity.__dict__, "credential_path": str(credential_path)},
    )
    monkeypatch.setenv("SUPABASE_URL", probe.origin)
    monkeypatch.setenv("SUPABASE_ANON_KEY", matrix.supabase_anon_key)
    try:
        with WheelbaseClient(timeout=2) as client:
            with pytest.raises(httpx.HTTPStatusError):
                client.postgrest_get("inventory_car", {"select": "id"})
        assert probe.requests == 1
    finally:
        probe.close()
        wheelbase_runtime.reset_identity(runtime_token)
        wheelbase_runtime.clear_task("store-outage")


def test_wheelbase_rls_auth(tmp_path, monkeypatch, caplog):
    raw_path = os.environ.get(_MATRIX_ENV, "").strip()
    if not raw_path:
        pytest.skip(f"{_MATRIX_ENV} absent; local Supabase RLS orchestrator not running")
    matrix = _load_matrix(Path(raw_path))
    monkeypatch.setenv("SUPABASE_URL", matrix.supabase_url)
    monkeypatch.setenv("SUPABASE_ANON_KEY", matrix.supabase_anon_key)

    # Exercise every concrete identity through the real profile-router app.
    # This is the boundary where tenant/dealer/client/device scope is carried;
    # the shared Supabase bearer by itself intentionally does not encode those
    # per-session fields.
    envelope_key = b"r" * 32
    monkeypatch.setenv(
        "AGENT_GATEWAY_IDENTITY_KEYS",
        json.dumps({"integration": base64.b64encode(envelope_key).decode()}),
    )
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "live-router-fixture-token")
    gateway_home = tmp_path / "gateway-home"
    gateway_home.mkdir(mode=0o700)
    gateway = _RealGatewayFixture(gateway_home)
    try:
        for case in matrix.cases:
            response = gateway.client.get(
                "/api/status",
                headers={
                    "X-Hermes-Session-Token": "live-router-fixture-token",
                    "X-Wheelbase-Identity-Envelope": _signed_envelope(
                        case, envelope_key
                    ),
                },
            )
            assert response.status_code == 200, case.name
        assert set(gateway.manager._children) == {
            (case.tenant_id, case.user_id) for case in matrix.cases
        }
    finally:
        gateway.close()

    for case in matrix.cases:
        # The multi-tenant and revoked alternate rows deliberately reuse the
        # same user's bearer. PostgREST resolves that bearer against the user's
        # one current active scope, so those negatives belong to the backend
        # session/scenario proof above, not to a contradictory direct JWT read.
        if case.name == "tenant-b-user-a-multi" or case.user_status != "active":
            continue
        jti_hash = hashlib.sha256(
            f"wheelbase-rls-test\0{case.name}\0{case.user_id}".encode()
        ).hexdigest()
        identity = WheelbaseIdentity(
            user_id=case.user_id,
            tenant_id=case.tenant_id,
            dealership_id=case.dealer_id,
            jwt=case.access_token,
            client=case.client,
            device_id=case.device_id or "",
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
                "dealership_id": case.dealer_id,
                "client": case.client,
                "device_id": case.device_id or "",
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

    log_text = "\n".join(record.message for record in caplog.records)
    assert all(case.access_token not in log_text for case in matrix.cases)
