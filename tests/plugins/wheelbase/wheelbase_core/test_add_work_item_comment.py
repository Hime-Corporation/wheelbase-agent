"""Tests for add_work_item_comment tool (Batch B5)."""

import json

import wheelbase_core.tools.add_work_item_comment as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, work_item_tenant="tenant-1", comment_id="cmt-1"):
        self.calls = []
        self._tenant = work_item_tenant
        self._comment_id = comment_id

    def postgrest_get(self, table, params):
        self.calls.append({"method": "GET", "table": table, "params": dict(params)})
        if table == "work_item":
            return [{"tenant_id": self._tenant}]
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})
        return [{"id": self._comment_id, "work_item_id": body.get("work_item_id"), "content": body.get("content")}]

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------

def test_add_comment_inserts_to_work_item_comment(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.add_work_item_comment({"workItemId": "wi-1", "content": "Looks good"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "POST")
    assert write["table"] == "work_item_comment"


def test_add_comment_uses_content_field_not_body(monkeypatch):
    """Schema column is 'content', NOT 'body'."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.add_work_item_comment({"workItemId": "wi-1", "content": "Check this"})
    write = next(c for c in client.calls if c["method"] == "POST")
    assert "content" in write["body"]
    assert write["body"]["content"] == "Check this"
    assert "body" not in write["body"]  # must NOT write a 'body' column


def test_add_comment_sets_work_item_id(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.add_work_item_comment({"workItemId": "wi-abc", "content": "Note"})
    write = next(c for c in client.calls if c["method"] == "POST")
    assert write["body"]["work_item_id"] == "wi-abc"


def test_add_comment_includes_tenant_id(monkeypatch):
    """Must include the tenant_id resolved from work_item."""
    client = FakeClient(work_item_tenant="ten-xyz")
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.add_work_item_comment({"workItemId": "wi-1", "content": "Something"})
    write = next(c for c in client.calls if c["method"] == "POST")
    assert write["body"]["tenant_id"] == "ten-xyz"


def test_add_comment_returns_comment_id(monkeypatch):
    client = FakeClient(comment_id="cmt-99")
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.add_work_item_comment({"workItemId": "wi-1", "content": "Done"}))
    assert out["commentId"] == "cmt-99"


def test_add_comment_fetches_work_item_for_tenant(monkeypatch):
    """Must query work_item table to resolve tenant_id."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.add_work_item_comment({"workItemId": "wi-1", "content": "OK"})
    gets = [c for c in client.calls if c["method"] == "GET"]
    assert any(c["table"] == "work_item" for c in gets)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_add_comment_requires_work_item_id():
    out = json.loads(mod.add_work_item_comment({"content": "Note"}))
    assert "error" in out
    assert "workItemId" in out["error"]


def test_add_comment_requires_content():
    out = json.loads(mod.add_work_item_comment({"workItemId": "wi-1"}))
    assert "error" in out
    assert "content" in out["error"]


def test_add_comment_empty_content_is_rejected():
    out = json.loads(mod.add_work_item_comment({"workItemId": "wi-1", "content": "   "}))
    assert "error" in out


def test_add_comment_work_item_not_found(monkeypatch):
    class _NoItem(FakeClient):
        def postgrest_get(self, table, params):
            self.calls.append({"method": "GET", "table": table, "params": params})
            return []

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: _NoItem())
    out = json.loads(mod.add_work_item_comment({"workItemId": "missing", "content": "Note"}))
    assert "error" in out
    assert "not found" in out["error"].lower() or "missing" in out["error"].lower()


def test_add_comment_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.add_work_item_comment({"workItemId": "wi-1", "content": "Note"}))
    assert out["error"] == "not_signed_in"
