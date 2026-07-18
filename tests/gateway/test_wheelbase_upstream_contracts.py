"""Pin the upstream Hermes API shapes the Wheelbase desktop/agent-gateway
consumes but that are otherwise undocumented in this repo: the dashboard
OAuth endpoints (`hermes_cli/web_server.py`) and the `/api/model/set`
model-assignment endpoint.

None of these tests hit the network, spawn a real OAuth flow, or spawn
subprocesses — they inspect the FastAPI route table and pydantic request
models directly (importing ``hermes_cli.web_server`` is the only "heavy"
step, and existing tests such as
``tests/hermes_cli/test_web_oauth_dispatch.py`` already do this at module
scope, so it's a well-trodden import path in this suite).

If a future upstream merge renames a route, drops a catalog provider id,
changes a submit-body field, or removes a request key from
``/api/model/set``, these tests fail loudly instead of Wheelbase's desktop
Accounts tab or model picker breaking silently in production.
"""

from __future__ import annotations

import inspect

from hermes_cli import web_server


# ---------------------------------------------------------------------------
# (a) Route table — dashboard OAuth + model-set endpoints Wheelbase calls.
#
# We iterate `app.routes` rather than issuing requests: several of these
# handlers touch profile resolution / background OAuth workers that are
# expensive or side-effectful to actually invoke just to prove the route
# exists. The other test modules under tests/hermes_cli/ already exercise
# the handlers themselves end-to-end; this test is scoped to "the route
# exists with this method+path", which is the contract surface Wheelbase's
# desktop HTTP client hardcodes.
# ---------------------------------------------------------------------------


def _route_table() -> set[tuple[str, str]]:
    """Return {(METHOD, path), ...} for every HTTP route on the app."""
    table: set[tuple[str, str]] = set()
    for route in web_server.app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            table.add((method, path))
    return table


def test_wheelbase_dashboard_oauth_and_model_routes_exist():
    routes = _route_table()

    required = {
        ("GET", "/api/providers/oauth"),
        ("POST", "/api/providers/oauth/{provider_id}/start"),
        ("POST", "/api/providers/oauth/{provider_id}/submit"),
        ("GET", "/api/providers/oauth/{provider_id}/poll/{session_id}"),
        ("DELETE", "/api/providers/oauth/sessions/{session_id}"),
        ("DELETE", "/api/providers/oauth/{provider_id}"),
        ("POST", "/api/model/set"),
    }

    missing = required - routes
    assert not missing, f"Wheelbase-depended-on routes missing from web_server.app: {missing}"


# ---------------------------------------------------------------------------
# (b) OAuth catalog — provider ids + flow values Wheelbase's Accounts tab
#     renders explicitly.
# ---------------------------------------------------------------------------


def test_oauth_catalog_has_expected_provider_ids_and_flows():
    catalog = web_server._build_oauth_catalog()
    by_id = {entry["id"]: entry for entry in catalog}

    required_ids = {"openai-codex", "anthropic", "xai-oauth"}
    missing = required_ids - set(by_id)
    assert not missing, f"OAuth catalog is missing provider ids Wheelbase depends on: {missing}"

    valid_flows = {"pkce", "device_code", "external"}
    for provider_id in required_ids:
        flow = by_id[provider_id].get("flow")
        assert flow in valid_flows, (
            f"{provider_id} has flow={flow!r}, expected one of {valid_flows}"
        )


def test_oauth_provider_catalog_base_also_has_expected_ids_and_flows():
    """Same pin against the raw ``_OAUTH_PROVIDER_CATALOG`` base tuple (not
    just the derived ``_build_oauth_catalog()`` view) — both are
    upstream-owned surfaces and either could silently drop an entry."""
    by_id = {entry["id"]: entry for entry in web_server._OAUTH_PROVIDER_CATALOG}

    required_ids = {"openai-codex", "anthropic", "xai-oauth"}
    missing = required_ids - set(by_id)
    assert not missing, f"_OAUTH_PROVIDER_CATALOG is missing provider ids: {missing}"

    valid_flows = {"pkce", "device_code", "external"}
    for provider_id in required_ids:
        flow = by_id[provider_id].get("flow")
        assert flow in valid_flows, (
            f"{provider_id} has flow={flow!r}, expected one of {valid_flows}"
        )


# ---------------------------------------------------------------------------
# (c) OAuth submit body model — fields the desktop PKCE-code-entry form
#     posts to /api/providers/oauth/{provider_id}/submit.
# ---------------------------------------------------------------------------


def test_oauth_submit_body_has_expected_fields():
    fields = web_server.OAuthSubmitBody.model_fields
    assert "session_id" in fields
    assert "code" in fields


# ---------------------------------------------------------------------------
# (d) /api/model/set request body — keys the desktop model picker posts.
# ---------------------------------------------------------------------------


def test_model_assignment_body_accepts_scope_provider_model():
    fields = web_server.ModelAssignment.model_fields
    for key in ("scope", "provider", "model"):
        assert key in fields, f"ModelAssignment is missing required field {key!r}"


# ---------------------------------------------------------------------------
# (e) OAuth session poll status literals.
#
# These are inline string literals assigned directly to `sess["status"]`
# throughout web_server.py rather than an enum, so there is no importable
# symbol to pin against type-safely. `_new_oauth_session` is the single
# place that documents the full contract in a comment (`# pending |
# approved | denied | expired | error`) next to the literal that creates
# every session in the "pending" state; every other status transition in
# the file assigns a value from that documented set (grep for
# `sess["status"] = "..."` confirms "approved"/"expired"/"error" are all
# actually assigned somewhere; "denied" is documented but not currently
# assigned anywhere in this file — pinned anyway since Wheelbase's poll
# client already handles it defensively).
#
# This is an inspect.getsource() tripwire, not a runtime contract: if
# upstream renames or removes this comment (e.g. while refactoring status
# handling), this test breaks and forces a human to re-verify the actual
# literals used across the file before updating the pin.
# ---------------------------------------------------------------------------


def test_oauth_session_status_literals_are_documented():
    source = inspect.getsource(web_server._new_oauth_session)
    assert '"status": "pending"' in source

    documented_literals_comment = source.split('"status": "pending",', 1)[1].splitlines()[0]
    for status in ("pending", "approved", "denied", "expired", "error"):
        assert status in documented_literals_comment, (
            f"expected {status!r} in the status-literal doc comment on "
            f"_new_oauth_session, got: {documented_literals_comment!r}"
        )


def test_oauth_session_status_literals_actually_used_in_file():
    """Belt-and-suspenders: confirm the non-pending literals are assigned
    somewhere in web_server.py, not just documented in a stale comment."""
    source = inspect.getsource(web_server)
    for status in ("approved", "expired", "error"):
        assert f'sess["status"] = "{status}"' in source, (
            f'expected sess["status"] = "{status}" to appear somewhere in '
            f"web_server.py"
        )
