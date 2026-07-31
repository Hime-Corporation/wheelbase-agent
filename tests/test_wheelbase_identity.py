"""Tests for tui_gateway.wheelbase_identity (Task B1).

TDD: written before implementation so they fail first, then pass after.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import base64
import hashlib
import hmac
import time
import uuid
from pathlib import Path

import pytest

from tui_gateway.wheelbase_identity import (
    WheelbaseIdentity,
    credential_path,
    identity_from_headers,
    remove_credential_file,
    write_credential_file,
    _attach_identity_to_transport,
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _envelope(bundle: dict, key: bytes, *, kid="k1", **claims) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    payload = {"iss": "wheelbase-api", "aud": "wheelbase-agent-gateway", "kind": "agent_gateway_identity", "ver": 2, "iat": now, "exp": now + 20, "nonce": str(uuid.uuid4()), "bundle": bundle, **claims}
    signing = f"{_b64(json.dumps(header, separators=(',', ':'), sort_keys=True).encode())}.{_b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())}"
    return f"{signing}.{_b64(hmac.new(key, signing.encode(), hashlib.sha256).digest())}"


@pytest.fixture
def envelope_env(monkeypatch):
    key = b"k" * 32
    monkeypatch.setenv("AGENT_GATEWAY_IDENTITY_KEYS", json.dumps({"k1": base64.b64encode(key).decode()}))
    return key


def _bundle(**overrides):
    return {"user_id": "user-123", "tenant_id": "tenant-123", "dealership_id": "dealer-1", "client": "desktop", "device_id": str(uuid.uuid4()), "session_jti_hash": "a" * 64, "credential_revision": 3, "credential_expires_at": int(time.time()) + 300, "access_token": "secret-token", "cdp_url": "wss://cdp", "shell_relay_url": "wss://shell", **overrides}


# ---------------------------------------------------------------------------
# identity_from_headers
# ---------------------------------------------------------------------------

class TestIdentityFromHeaders:
    def test_signed_envelope_constructs_atomic_identity(self, envelope_env):
        identity = identity_from_headers({"X-Wheelbase-Identity-Envelope": _envelope(_bundle(), envelope_env)})
        assert identity is not None
        assert identity.session_jti_hash == "a" * 64
        assert identity.credential_revision == 3
        assert identity.credential_expires_at > int(time.time())

    @pytest.mark.parametrize("mutated", ["signature", "issuer", "audience", "kind", "version", "kid", "expired", "ttl"])
    def test_invalid_envelope_is_rejected(self, envelope_env, mutated):
        kwargs = {}
        kid = "k1"
        if mutated == "issuer": kwargs["iss"] = "other"
        if mutated == "audience": kwargs["aud"] = "other"
        if mutated == "kind": kwargs["kind"] = "exec"
        if mutated == "version": kwargs["ver"] = 1
        if mutated == "kid": kid = "unknown"
        if mutated == "expired": kwargs.update(iat=int(time.time()) - 20, exp=int(time.time()) - 1)
        if mutated == "ttl": kwargs.update(iat=int(time.time()), exp=int(time.time()) + 31)
        token = _envelope(_bundle(), envelope_env, kid=kid, **kwargs)
        if mutated == "signature": token = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError):
            identity_from_headers({"X-Wheelbase-Identity-Envelope": token})

    def test_independent_identity_header_is_rejected_even_with_envelope(self, envelope_env):
        with pytest.raises(ValueError):
            identity_from_headers({"X-Wheelbase-Identity-Envelope": _envelope(_bundle(), envelope_env), "X-Wheelbase-User-Id": "user-123"})
    def test_empty_headers_returns_none(self):
        assert identity_from_headers({}) is None

    def test_unsigned_identity_headers_are_rejected(self):
        with pytest.raises(ValueError):
            identity_from_headers({"X-Wheelbase-User-Id": "user-123"})

    def test_signed_mobile_identity_has_no_device(self, envelope_env):
        identity = identity_from_headers({"X-Wheelbase-Identity-Envelope": _envelope(_bundle(client="mobile", device_id=""), envelope_env)})
        assert identity is not None
        assert identity.client == "mobile"
        assert identity.device_id == ""


# ---------------------------------------------------------------------------
# write_credential_file
# ---------------------------------------------------------------------------

class TestWriteCredentialFile:
    def test_writes_json_with_access_token(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-1", jwt="tok123", session_jti_hash="jti-a", credential_revision=1, credential_expires_at=9999999999)
        path = write_credential_file(tmp_path, identity)
        data = json.loads(path.read_text())
        assert data["access_token"] == "tok123"
        assert "expires_at" in data

    def test_file_mode_0600(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-2", jwt="tok456", session_jti_hash="jti-2", credential_revision=1, credential_expires_at=9999999999)
        path = write_credential_file(tmp_path, identity)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_credential_path_structure(self, tmp_path):
        p = credential_path(tmp_path, "jti-abc")
        assert p == tmp_path / "wheelbase-sessions" / "jti-abc.json"

    def test_creates_parent_directory(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-4", jwt="token", session_jti_hash="jti-4", credential_revision=1, credential_expires_at=9999999999)
        path = write_credential_file(tmp_path, identity)
        assert path.exists()
        assert (tmp_path / "wheelbase-sessions").is_dir()


# ---------------------------------------------------------------------------
# revision and JTI isolation
# ---------------------------------------------------------------------------

class TestCredentialRevision:
    def _identity(self, jti, revision, token):
        return WheelbaseIdentity(user_id="same-user", tenant_id="tenant", jwt=token,
            session_jti_hash=jti, credential_revision=revision,
            credential_expires_at=9999999999, credential_source="agent_session")

    def test_two_connections_for_one_user_have_distinct_files(self, tmp_path):
        first = write_credential_file(tmp_path, self._identity("jti-one", 1, "one"))
        second = write_credential_file(tmp_path, self._identity("jti-two", 1, "two"))
        assert first != second
        assert json.loads(first.read_text())["access_token"] == "one"
        assert json.loads(second.read_text())["access_token"] == "two"

    def test_older_or_equal_revision_cannot_overwrite(self, tmp_path):
        path = write_credential_file(tmp_path, self._identity("jti-one", 2, "new"))
        write_credential_file(tmp_path, self._identity("jti-one", 1, "old"))
        write_credential_file(tmp_path, self._identity("jti-one", 2, "equal"))
        assert json.loads(path.read_text())["access_token"] == "new"

    def test_cleanup_removes_only_one_jti(self, tmp_path):
        first = write_credential_file(tmp_path, self._identity("jti-one", 1, "one"))
        second = write_credential_file(tmp_path, self._identity("jti-two", 1, "two"))
        assert remove_credential_file(tmp_path, "jti-one") is True
        assert not first.exists()
        assert second.exists()


# ---------------------------------------------------------------------------
# transport attachment helper
# ---------------------------------------------------------------------------

class TestAttachIdentityToTransport:
    def test_attaches_identity_when_envelope_present(self, envelope_env):
        class FakeWS:
            headers = {"x-wheelbase-identity-envelope": _envelope(_bundle(user_id="user-ws-1", tenant_id="t-99"), envelope_env)}

        class FakeTransport:
            pass

        transport = FakeTransport()
        ws = FakeWS()
        _attach_identity_to_transport(ws, transport)
        assert transport.wheelbase_identity is not None
        assert transport.wheelbase_identity.user_id == "user-ws-1"
        assert transport.wheelbase_identity.tenant_id == "t-99"

    def test_sets_none_when_no_user_header(self):
        class FakeWS:
            headers = {"x-other-header": "value"}

        class FakeTransport:
            pass

        transport = FakeTransport()
        _attach_identity_to_transport(FakeWS(), transport)
        assert transport.wheelbase_identity is None

    def test_sets_none_when_no_headers_attr(self):
        class FakeWS:
            pass

        class FakeTransport:
            pass

        transport = FakeTransport()
        _attach_identity_to_transport(FakeWS(), transport)
        assert transport.wheelbase_identity is None

    def test_header_access_exception_is_not_downgraded_to_anonymous(self):
        class FakeWS:
            @property
            def headers(self):
                raise RuntimeError("boom")

        class FakeTransport:
            pass

        transport = FakeTransport()
        with pytest.raises(RuntimeError, match="boom"):
            _attach_identity_to_transport(FakeWS(), transport)
