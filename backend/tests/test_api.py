"""HTTP API contract tests through FastAPI's TestClient.

These tests exercise authentication, RBAC, the CRUD surface and the honest
host-unavailable behaviour of change requests.  They do not claim that any
Linux data-plane feature works on this Windows development host.
"""
from __future__ import annotations

from uuid import uuid4

from tests.conftest import headers_for


def _suffix() -> str:
    return uuid4().hex[:10]


def test_health_is_public_and_api_requires_authentication(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database"]["dialect"] == "sqlite"

    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"}
    ).status_code == 401
    assert client.get("/api/v1/dashboard").status_code == 401


def test_login_rejects_bad_password(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "definitely-wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_viewer_can_read_but_cannot_write(client, viewer_user):
    headers = headers_for(client, viewer_user.username, "UserPassw0rd!xyz")
    assert client.get("/api/v1/dashboard", headers=headers).status_code == 200
    assert client.get("/api/v1/sessions", headers=headers).status_code == 200
    assert client.get("/api/v1/users", headers=headers).status_code == 403

    denied = client.post(
        "/api/v1/pools",
        headers=headers,
        json={"name": f"viewer-pool-{_suffix()}"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"


def test_pool_and_address_crud(client, auth_headers):
    name = f"pool-{_suffix()}"
    created = client.post(
        "/api/v1/pools", headers=auth_headers, json={"name": name, "description": "api test"}
    )
    assert created.status_code == 201, created.text
    pool_id = created.json()["id"]

    duplicate = client.post("/api/v1/pools", headers=auth_headers, json={"name": name})
    assert duplicate.status_code == 409

    listed = client.get("/api/v1/pools", headers=auth_headers)
    assert listed.status_code == 200
    assert any(item["id"] == pool_id for item in listed.json()["items"])

    address = client.post(
        f"/api/v1/pools/{pool_id}/addresses",
        headers=auth_headers,
        json={"address": "198.51.100.42", "port_min": 30000, "port_max": 31000},
    )
    assert address.status_code == 201, address.text
    address_id = address.json()["id"]
    assert address.json()["status"] == "pending"

    fetched = client.get(f"/api/v1/addresses/{address_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["usage_basis"] == "observed_active_sessions"
    assert fetched.json()["sessions"] == 0

    updated = client.patch(
        f"/api/v1/addresses/{address_id}", headers=auth_headers, json={"label": "spare"}
    )
    assert updated.status_code == 200

    overlap = client.post(
        f"/api/v1/pools/{pool_id}/addresses",
        headers=auth_headers,
        json={"address": "198.51.100.42", "port_min": 30500, "port_max": 31500},
    )
    assert overlap.status_code == 409

    imported = client.post(
        f"/api/v1/pools/{pool_id}/addresses/import",
        headers=auth_headers,
        json={"text": "198.51.100.50\n198.51.100.51", "port_min": 40000, "port_max": 41000},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["created"] == ["198.51.100.50", "198.51.100.51"]

    deleted = client.delete(f"/api/v1/addresses/{address_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/addresses/{address_id}", headers=auth_headers).status_code == 404

    removed = client.delete(f"/api/v1/pools/{pool_id}", headers=auth_headers)
    assert removed.status_code == 200


def test_host_changes_are_created_but_dry_run_fails_honestly(client, auth_headers):
    """Windows has no Jool/BIND/nftables helper: plans must say so, not fake success."""
    response = client.post(
        "/api/v1/dns64/apply",
        headers=auth_headers,
        json={"enabled": True, "prefix": "64:ff9b::/96"},
    )
    assert response.status_code == 202, response.text
    change = response.json()
    assert change["status"] == "dry_run_failed"
    assert change["plan"]["host_available"] is False
    assert change["plan"]["errors"]
    assert "linux" in " ".join(change["plan"]["errors"]).lower()

    for action, path, payload in [
        # Disabled firewall avoids the (separate) management-allowlist guard,
        # so this exercises the host-unavailable path.
        ("firewall.apply", "/api/v1/firewall/apply", {"enabled": False}),
        ("network.apply", "/api/v1/network/apply", {"mtu": 1400}),
    ]:
        result = client.post(path, headers=auth_headers, json=payload)
        assert result.status_code == 202, (action, result.text)
        body = result.json()
        assert body["action"] == action
        assert body["status"] == "dry_run_failed"
        assert body["plan"]["errors"]
        assert body["plan"]["host_available"] is False

    # Invalid host state must also prevent an accidental execute attempt.
    denied = client.post(
        f"/api/v1/changes/{change['id']}/execute", headers=auth_headers
    )
    assert denied.status_code == 409


def test_unsupported_host_status_is_reported(client, auth_headers):
    jool = client.get("/api/v1/jool/status", headers=auth_headers)
    assert jool.status_code == 200
    assert jool.json()["status"] == "unsupported"

    dns = client.get("/api/v1/dns64/status", headers=auth_headers)
    assert dns.status_code == 200
    assert dns.json()["status"] == "unsupported"
    assert dns.json()["active"] is False

    firewall = client.get("/api/v1/firewall/status", headers=auth_headers)
    assert firewall.status_code == 200
    assert firewall.json()["status"] == "disabled"
    assert firewall.json()["enabled"] is False


def test_firewall_rule_crud_is_database_only(client, auth_headers):
    created = client.post(
        "/api/v1/firewall/rules",
        headers=auth_headers,
        json={
            "name": f"rule-{_suffix()}",
            "direction": "input",
            "action": "accept",
            "protocol": "tcp",
            "destination_port": 443,
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["last_applied_at"] is None

    listed = client.get("/api/v1/firewall/rules", headers=auth_headers)
    assert any(item["id"] == rule_id for item in listed.json()["items"])

    deleted = client.delete(f"/api/v1/firewall/rules/{rule_id}", headers=auth_headers)
    assert deleted.status_code == 200


def test_backup_create_validate_download_delete(client, auth_headers):
    created = client.post(
        "/api/v1/backups",
        headers=auth_headers,
        json={"name": f"backup-{_suffix()}", "note": "api test"},
    )
    assert created.status_code == 201, created.text
    backup_id = created.json()["id"]
    assert created.json()["checksum"]

    validated = client.get(f"/api/v1/backups/{backup_id}/validate", headers=auth_headers)
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    download = client.get(f"/api/v1/backups/{backup_id}/download", headers=auth_headers)
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")

    listed = client.get("/api/v1/backups", headers=auth_headers)
    assert any(item["id"] == backup_id for item in listed.json()["items"])

    deleted = client.delete(f"/api/v1/backups/{backup_id}", headers=auth_headers)
    assert deleted.status_code == 200


def test_csv_exports_and_audit_are_available(client, auth_headers):
    sessions = client.get("/api/v1/sessions/export.csv", headers=auth_headers)
    assert sessions.status_code == 200
    assert "text/csv" in sessions.headers["content-type"]
    assert "client_ipv6" in sessions.text.splitlines()[0]

    audit = client.get("/api/v1/audit/export.csv", headers=auth_headers)
    assert audit.status_code == 200
    assert "text/csv" in audit.headers["content-type"]

    actions = client.get("/api/v1/audit/actions", headers=auth_headers)
    assert actions.status_code == 200
    assert actions.json()["items"]


def test_wizard_state_and_settings(client, auth_headers):
    state = client.get("/api/v1/wizard", headers=auth_headers)
    assert state.status_code == 200
    assert len(state.json()["definitions"]) == 11
    assert state.json()["definitions"][0]["key"] == "host"
    assert state.json()["current_step"] == 1

    settings = client.get("/api/v1/settings/application", headers=auth_headers)
    assert settings.status_code == 200
    assert settings.json()["app"]["version"]

    unknown = client.get("/api/v1/settings/not-a-key", headers=auth_headers)
    assert unknown.status_code == 404


def test_network_preview_never_claims_host_validation(client, auth_headers):
    preview = client.post(
        "/api/v1/network/preview",
        headers=auth_headers,
        json={"wan4_interface": "wan0", "mtu": 1400},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["validated_on_host"] is False
    assert body["host_available"] is False
    assert "version: 2" in body["netplan_yaml"]


def test_write_is_visible_to_an_immediate_read(client, auth_headers):
    """The transaction must commit before the client receives the response."""
    check = client.post("/api/v1/wizard/check", headers=auth_headers)
    assert check.status_code == 200
    checked_at = check.json()["checked_at"]

    state = client.get("/api/v1/wizard", headers=auth_headers)
    assert state.status_code == 200
    assert state.json()["last_check"]["checked_at"] == checked_at


def test_ha_preview_redacts_the_vrrp_secret(client, auth_headers):
    secret = "VerifyPass123!"
    update = client.patch(
        "/api/v1/ha/settings",
        headers=auth_headers,
        json={
            "enabled": True,
            "mode": "keepalived",
            "virtual_ip": "203.0.113.10",
            "vrrp_interface": "eth0",
            "vrrp_router_id": 51,
            "priority": 150,
            "auth_secret": secret,
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["settings"]["auth_secret_set"] is True
    assert secret not in update.text

    preview = client.get("/api/v1/ha/preview", headers=auth_headers)
    assert preview.status_code == 200
    body = preview.json()
    assert body["content"]
    assert secret not in body["content"]
    assert "auth_pass ********" in body["content"]
    assert body["settings"]["auth_secret_set"] is True
