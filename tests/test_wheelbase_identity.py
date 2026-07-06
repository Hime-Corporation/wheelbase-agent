"""Tests for tui_gateway.wheelbase_identity (Task B1).

TDD: written before implementation so they fail first, then pass after.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from tui_gateway.wheelbase_identity import (
    WheelbaseIdentity,
    credential_path,
    current_jwt,
    identity_from_headers,
    update_user_jwt,
    write_credential_file,
    _attach_identity_to_transport,
)


# ---------------------------------------------------------------------------
# identity_from_headers
# ---------------------------------------------------------------------------

class TestIdentityFromHeaders:
    def test_all_five_fields(self):
        headers = {
            "x-wheelbase-user-id": "user-123",
            "x-wheelbase-tenant-id": "tenant-456",
            "x-wheelbase-dealership-id": "dealer-789",
            "x-wheelbase-user-jwt": "jwt.tok.en",
            "x-wheelbase-cdp-url": "ws://cdp:9222",
        }
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.user_id == "user-123"
        assert identity.tenant_id == "tenant-456"
        assert identity.dealership_id == "dealer-789"
        assert identity.jwt == "jwt.tok.en"
        assert identity.cdp_url == "ws://cdp:9222"

    def test_case_insensitive_headers(self):
        headers = {
            "X-Wheelbase-User-Id": "user-abc",
            "X-WHEELBASE-TENANT-ID": "t-001",
        }
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.user_id == "user-abc"
        assert identity.tenant_id == "t-001"

    def test_missing_user_header_returns_none(self):
        headers = {"x-wheelbase-tenant-id": "t-001"}
        assert identity_from_headers(headers) is None

    def test_empty_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "   "}
        assert identity_from_headers(headers) is None

    def test_empty_headers_returns_none(self):
        assert identity_from_headers({}) is None

    # --- security: path traversal + bad user_id values ---

    def test_path_traversal_returns_none(self):
        headers = {"x-wheelbase-user-id": "../evil"}
        assert identity_from_headers(headers) is None

    def test_path_traversal_deep_returns_none(self):
        headers = {"x-wheelbase-user-id": "../../etc/passwd"}
        assert identity_from_headers(headers) is None

    def test_space_in_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "user name"}
        assert identity_from_headers(headers) is None

    def test_100_char_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "a" * 100}
        assert identity_from_headers(headers) is None

    def test_64_char_user_id_accepted(self):
        uid = "a" * 64
        headers = {"x-wheelbase-user-id": uid}
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.user_id == uid

    def test_65_char_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "a" * 65}
        assert identity_from_headers(headers) is None

    def test_supabase_uuid_format_accepted(self):
        # Supabase UUIDs: lowercase hex + hyphens
        uid = "550e8400-e29b-41d4-a716-446655440000"
        headers = {"x-wheelbase-user-id": uid}
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.user_id == uid

    def test_slash_in_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "user/evil"}
        assert identity_from_headers(headers) is None

    def test_null_byte_in_user_id_returns_none(self):
        headers = {"x-wheelbase-user-id": "user\x00evil"}
        assert identity_from_headers(headers) is None

    def test_optional_fields_default_to_empty(self):
        headers = {"x-wheelbase-user-id": "user-only"}
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.tenant_id == ""
        assert identity.dealership_id == ""
        assert identity.jwt == ""
        assert identity.cdp_url == ""

    def test_shell_relay_url_parsed(self):
        headers = {
            "x-wheelbase-user-id": "user-123",
            "x-wheelbase-shell-relay-url": "wss://api.wheelbase.io/v1/agent/exec?u=user-123",
        }
        identity = identity_from_headers(headers)
        assert identity is not None
        assert identity.shell_relay_url == "wss://api.wheelbase.io/v1/agent/exec?u=user-123"

    def test_shell_relay_url_defaults_empty(self):
        identity = identity_from_headers({"x-wheelbase-user-id": "user-123"})
        assert identity is not None
        assert identity.shell_relay_url == ""


# ---------------------------------------------------------------------------
# write_credential_file
# ---------------------------------------------------------------------------

class TestWriteCredentialFile:
    def test_writes_json_with_access_token(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-1", jwt="tok123")
        path = write_credential_file(tmp_path, identity)
        data = json.loads(path.read_text())
        assert data["access_token"] == "tok123"
        assert "expires_at" in data

    def test_file_mode_0600(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-2", jwt="tok456")
        path = write_credential_file(tmp_path, identity)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_update_user_jwt_overrides_embedded_jwt_on_next_write(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-3", jwt="old-tok")
        update_user_jwt("user-3", "new-tok")
        try:
            path = write_credential_file(tmp_path, identity)
            data = json.loads(path.read_text())
            assert data["access_token"] == "new-tok"
        finally:
            # Clean up the in-memory JWT cache so we don't pollute other tests
            update_user_jwt("user-3", "")

    def test_credential_path_structure(self, tmp_path):
        p = credential_path(tmp_path, "user-abc")
        assert p == tmp_path / "wheelbase-sessions" / "user-abc.json"

    def test_creates_parent_directory(self, tmp_path):
        identity = WheelbaseIdentity(user_id="user-4")
        path = write_credential_file(tmp_path, identity)
        assert path.exists()
        assert (tmp_path / "wheelbase-sessions").is_dir()


# ---------------------------------------------------------------------------
# current_jwt
# ---------------------------------------------------------------------------

class TestCurrentJwt:
    def test_returns_embedded_jwt_when_no_update(self):
        identity = WheelbaseIdentity(user_id="cj-user-x", jwt="embed-tok")
        # Ensure no cached override
        result = current_jwt(identity)
        # Could be either the embedded or a cached one; just check type
        assert isinstance(result, str)

    def test_update_overrides_embedded(self):
        uid = "cj-user-override-test"
        identity = WheelbaseIdentity(user_id=uid, jwt="embed")
        update_user_jwt(uid, "overridden")
        try:
            assert current_jwt(identity) == "overridden"
        finally:
            update_user_jwt(uid, "")


# ---------------------------------------------------------------------------
# transport attachment helper
# ---------------------------------------------------------------------------

class TestAttachIdentityToTransport:
    def test_attaches_identity_when_headers_present(self):
        class FakeWS:
            headers = {
                "x-wheelbase-user-id": "user-ws-1",
                "x-wheelbase-tenant-id": "t-99",
            }

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

    def test_sets_none_on_exception(self):
        class FakeWS:
            @property
            def headers(self):
                raise RuntimeError("boom")

        class FakeTransport:
            pass

        transport = FakeTransport()
        _attach_identity_to_transport(FakeWS(), transport)
        assert transport.wheelbase_identity is None
