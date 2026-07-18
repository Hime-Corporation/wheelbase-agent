"""Pin the global-root auth.json walk-up that Wheelbase's per-tenant
shared-credentials design depends on.

Wheelbase runs one Hermes profile per tenant *user*, nested under a
per-tenant root: ``<tenant_root>/profiles/wb-<user_id>``. Because the
immediate parent directory of ``HERMES_HOME`` is named ``profiles``,
``hermes_constants.get_default_hermes_root()`` walks up to the
*grandparent* and treats it as the shared root for that tenant. Wheelbase
uses this to let every user-profile under a tenant inherit provider
credentials (e.g. a shared Nous/Anthropic login) authenticated once at the
tenant root, via the global-root auth.json fallback wired into
``hermes_cli/auth.py``'s ``_load_provider_state`` / `_load_provider_state_with_source`.

If upstream ever changes the "parent dir literally named 'profiles' -> walk
up two levels" rule, or changes the fallback-vs-shadow semantics of
``_load_provider_state``, Wheelbase's shared-tenant-credentials assumption
silently breaks. These tests pin the current, verified behavior so such a
change fails CI instead of production.

See also ``tests/hermes_cli/test_auth_profile_fallback.py`` and
``tests/test_hermes_constants.py::TestGetDefaultHermesRoot`` for the
upstream-owned test suites this file deliberately does not duplicate —
this file is scoped narrowly to the tenant/profile directory shape
Wheelbase actually uses in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _make_auth_store(providers: dict | None = None) -> dict:
    store: dict = {"version": 1}
    if providers is not None:
        store["providers"] = providers
    return store


@pytest.fixture()
def tenant_env(tmp_path, monkeypatch):
    """Build the Wheelbase on-disk layout: ``<tmp>/tenants/<tid>/profiles/wb-u1``.

    This is NOT under ``Path.home()/.hermes`` — it mirrors the real
    Wheelbase Docker/custom deployment layout, so ``get_default_hermes_root()``
    takes the "custom HERMES_HOME, parent dir named 'profiles'" branch
    rather than the "under the native ~/.hermes tree" branch. Path.home()
    is redirected to a location the test never populates, so the two
    branches can never accidentally collide.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "unused-home")

    tenants_root = tmp_path / "tenants"
    tenant_root = tenants_root / "acme-motors"
    profile_dir = tenant_root / "profiles" / "wb-u1"
    profile_dir.mkdir(parents=True)

    other_tenant_root = tenants_root / "other-dealer"
    other_tenant_root.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(profile_dir))

    return {
        "tenant_root": tenant_root,
        "profile_dir": profile_dir,
        "other_tenant_root": other_tenant_root,
    }


# ---------------------------------------------------------------------------
# (a) get_default_hermes_root() walks up profile -> tenant root
# ---------------------------------------------------------------------------


def test_tenant_root_is_grandparent_of_profile(tenant_env):
    """HERMES_HOME=<tenant_root>/profiles/wb-u1 resolves to <tenant_root>.

    This is the property the Wheelbase per-tenant shared-credentials design
    rests on: every user profile under one tenant reports the same root.
    """
    from hermes_constants import get_default_hermes_root

    assert get_default_hermes_root() == tenant_env["tenant_root"]


def test_sibling_user_profile_resolves_to_same_tenant_root(tenant_env, monkeypatch):
    """A second user profile under the SAME tenant also resolves to the
    same root — the property that makes credential sharing correct across
    every user in a tenant, not just the one whose profile happened to
    authenticate.
    """
    from hermes_constants import get_default_hermes_root

    sibling_profile = tenant_env["tenant_root"] / "profiles" / "wb-u2"
    sibling_profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(sibling_profile))

    assert get_default_hermes_root() == tenant_env["tenant_root"]


# ---------------------------------------------------------------------------
# (b) _global_auth_file_path() points at <tenant_root>/auth.json
# ---------------------------------------------------------------------------


def test_global_auth_file_path_points_at_tenant_root(tenant_env):
    from hermes_cli.auth import _global_auth_file_path

    assert _global_auth_file_path() == tenant_env["tenant_root"] / "auth.json"


# ---------------------------------------------------------------------------
# (c) provider state present ONLY in the tenant-root auth.json is visible
#     from the profile (fallback works)
# ---------------------------------------------------------------------------


def test_provider_state_visible_via_tenant_root_fallback(tenant_env):
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    _write(
        tenant_env["tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "tenant-root-token", "refresh_token": "rt-root"},
        }),
    )
    # Profile auth.json exists but has no entry for this provider.
    _write(tenant_env["profile_dir"] / "auth.json", _make_auth_store(providers={}))

    auth_store = _load_auth_store()
    state = _load_provider_state(auth_store, "anthropic")

    assert state is not None
    assert state["access_token"] == "tenant-root-token"


def test_provider_state_with_source_reports_tenant_root_path(tenant_env):
    """The source-tracking variant reports the tenant-root path it read
    from, which is load-bearing for refresh-token write-through (a rotated
    refresh token must be persisted back to the store it came from, not
    the profile)."""
    from hermes_cli.auth import _load_auth_store, _load_provider_state_with_source

    _write(
        tenant_env["tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "tenant-root-token"},
        }),
    )
    _write(tenant_env["profile_dir"] / "auth.json", _make_auth_store(providers={}))

    auth_store = _load_auth_store()
    state, source_path = _load_provider_state_with_source(auth_store, "anthropic")

    assert state is not None
    assert state["access_token"] == "tenant-root-token"
    assert source_path == tenant_env["tenant_root"] / "auth.json"


# ---------------------------------------------------------------------------
# (d) provider state present in BOTH -> profile-local wins (shadowing)
# ---------------------------------------------------------------------------


def test_profile_local_provider_state_shadows_tenant_root(tenant_env):
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    _write(
        tenant_env["tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "tenant-root-token"},
        }),
    )
    _write(
        tenant_env["profile_dir"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "profile-local-token"},
        }),
    )

    auth_store = _load_auth_store()
    state = _load_provider_state(auth_store, "anthropic")

    assert state is not None
    assert state["access_token"] == "profile-local-token"


def test_shadowing_is_per_provider_not_whole_file(tenant_env):
    """A profile that has authed one provider still inherits OTHER
    providers from the tenant root — shadowing is per-provider-id, not an
    all-or-nothing switch once the profile file has any content."""
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    _write(
        tenant_env["tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "tenant-anthropic"},
            "nous": {"access_token": "tenant-nous"},
        }),
    )
    _write(
        tenant_env["profile_dir"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "profile-anthropic"},
            # No "nous" entry locally -> must still fall back.
        }),
    )

    auth_store = _load_auth_store()
    assert _load_provider_state(auth_store, "anthropic")["access_token"] == "profile-anthropic"
    assert _load_provider_state(auth_store, "nous")["access_token"] == "tenant-nous"


# ---------------------------------------------------------------------------
# (e) a sibling tenant's root is NOT consulted
# ---------------------------------------------------------------------------


def test_sibling_tenant_root_is_not_consulted(tenant_env):
    """Cross-tenant credential leakage guard: a provider authenticated at a
    DIFFERENT tenant's root must never be visible to this tenant's profile,
    even though both tenants share the same ``tenants/`` parent directory.
    """
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    # Sibling tenant has its own root auth.json with the provider configured.
    _write(
        tenant_env["other_tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "OTHER-TENANT-SECRET"},
        }),
    )
    # This tenant's root has nothing, and neither does the profile.
    _write(tenant_env["profile_dir"] / "auth.json", _make_auth_store(providers={}))

    auth_store = _load_auth_store()
    state = _load_provider_state(auth_store, "anthropic")

    assert state is None


def test_sibling_tenant_root_not_consulted_even_when_own_root_has_other_providers(tenant_env):
    """Same guard, but with THIS tenant's root populated for a different
    provider — proves the resolver targets exactly one computed root path
    (this tenant's), never falls through to scanning siblings."""
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    _write(
        tenant_env["other_tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "anthropic": {"access_token": "OTHER-TENANT-SECRET"},
        }),
    )
    _write(
        tenant_env["tenant_root"] / "auth.json",
        _make_auth_store(providers={
            "nous": {"access_token": "this-tenant-nous"},
        }),
    )
    _write(tenant_env["profile_dir"] / "auth.json", _make_auth_store(providers={}))

    auth_store = _load_auth_store()
    assert _load_provider_state(auth_store, "anthropic") is None
    assert _load_provider_state(auth_store, "nous")["access_token"] == "this-tenant-nous"


# ---------------------------------------------------------------------------
# Safety nets: missing / malformed tenant-root auth.json must not break reads
# ---------------------------------------------------------------------------


def test_missing_tenant_root_auth_file_is_safe(tenant_env):
    from hermes_cli.auth import _load_auth_store, _load_provider_state

    # No tenant-root auth.json written at all.
    _write(
        tenant_env["profile_dir"] / "auth.json",
        _make_auth_store(providers={"nous": {"access_token": "profile-only"}}),
    )

    auth_store = _load_auth_store()
    assert _load_provider_state(auth_store, "nous")["access_token"] == "profile-only"
    assert _load_provider_state(auth_store, "anthropic") is None


def test_malformed_tenant_root_auth_file_does_not_break_profile_read(tenant_env):
    (tenant_env["tenant_root"] / "auth.json").write_text("{not valid json")
    _write(
        tenant_env["profile_dir"] / "auth.json",
        _make_auth_store(providers={"nous": {"access_token": "profile-only"}}),
    )

    from hermes_cli.auth import _load_auth_store, _load_provider_state

    auth_store = _load_auth_store()
    assert _load_provider_state(auth_store, "nous")["access_token"] == "profile-only"
    assert _load_provider_state(auth_store, "anthropic") is None
