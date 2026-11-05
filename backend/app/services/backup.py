from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.models import (
    ConfigurationBackup,
    Dns64Settings,
    FirewallRule,
    FirewallSettings,
    IPv4Address,
    IPv4Pool,
    IPv6Prefix,
    JoolInstance,
    NetworkSettings,
    Setting,
    User,
)
from app.services.audit import write_audit

log = logging.getLogger(__name__)
BACKUP_SCHEMA = 1


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_config(db: Session) -> dict:
    pools = []
    for pool in db.scalars(select(IPv4Pool).order_by(IPv4Pool.name)).all():
        pools.append(
            {
                "name": pool.name,
                "description": pool.description,
                "enabled": pool.enabled,
                "jool_instance": pool.jool_instance.name if pool.jool_instance else None,
                "color": pool.color,
                "addresses": [
                    {
                        "address": address.address,
                        "port_min": address.port_min,
                        "port_max": address.port_max,
                        "enabled": address.enabled,
                        "label": address.label,
                    }
                    for address in sorted(pool.addresses, key=lambda a: (a.address, a.port_min))
                ],
            }
        )
    prefixes = [
        {
            "prefix": prefix.prefix,
            "name": prefix.name,
            "interface": prefix.interface,
            "vlan_id": prefix.vlan_id,
            "is_client_prefix": prefix.is_client_prefix,
            "enabled": prefix.enabled,
            "description": prefix.description,
        }
        for prefix in db.scalars(select(IPv6Prefix).order_by(IPv6Prefix.prefix)).all()
    ]
    instances = [
        {
            "name": instance.name,
            "framework": instance.framework,
            "pool6": instance.pool6,
            "namespace": instance.namespace,
            "display_name": instance.display_name,
            "enabled": instance.enabled,
        }
        for instance in db.scalars(select(JoolInstance).order_by(JoolInstance.name)).all()
    ]
    dns = db.get(Dns64Settings, 1)
    firewall = db.get(FirewallSettings, 1)
    network = db.get(NetworkSettings, 1)
    rules = [
        {
            "name": rule.name,
            "description": rule.description,
            "enabled": rule.enabled,
            "priority": rule.priority,
            "family": rule.family,
            "direction": rule.direction,
            "action": rule.action,
            "protocol": rule.protocol,
            "source_cidr": rule.source_cidr,
            "destination_cidr": rule.destination_cidr,
            "source_port": rule.source_port,
            "destination_port": rule.destination_port,
            "rate_limit_per_second": rule.rate_limit_per_second,
            "log": rule.log,
        }
        for rule in db.scalars(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.id)).all()
    ]
    app_settings = [
        {"key": item.key, "value": item.value, "description": item.description}
        for item in db.scalars(select(Setting).order_by(Setting.key)).all()
    ]
    return {
        "schema": BACKUP_SCHEMA,
        "app": "nat64-isp-manager",
        "app_version": settings.app_version,
        "exported_at": datetime.now(UTC).isoformat(),
        "ipv4_pools": pools,
        "ipv6_prefixes": prefixes,
        "jool_instances": instances,
        "dns64": {
            "enabled": dns.enabled,
            "prefix": dns.prefix,
            "upstreams": dns.upstreams,
            "listen_address": dns.listen_address,
            "service_name": dns.service_name,
            "managed_config_path": dns.managed_config_path,
            "test_domain": dns.test_domain,
            "exclude_prefixes": dns.exclude_prefixes,
            "forward_only": dns.forward_only,
            "dnssec_validation": dns.dnssec_validation,
        }
        if dns
        else None,
        "firewall": {
            "settings": {
                "enabled": firewall.enabled,
                "default_forward_policy": firewall.default_forward_policy,
                "default_input_policy": firewall.default_input_policy,
                "new_connection_rate": firewall.new_connection_rate,
                "rate_burst": firewall.rate_burst,
                "log_dropped": firewall.log_dropped,
                "management_allowlist": firewall.management_allowlist,
                "management_ports": firewall.management_ports,
                "protect_control_plane": firewall.protect_control_plane,
                "syn_flood_protection": firewall.syn_flood_protection,
                "invalid_packet_protection": firewall.invalid_packet_protection,
                "mss_clamping": firewall.mss_clamping,
            }
            if firewall
            else None,
            "rules": rules,
        },
        "network": {
            "wan4_interface": network.wan4_interface,
            "wan6_interface": network.wan6_interface,
            "lan6_interface": network.lan6_interface,
            "client_prefix": network.client_prefix,
            "nat64_prefix": network.nat64_prefix,
            "dns_server": network.dns_server,
            "default_gateway4": network.default_gateway4,
            "default_gateway6": network.default_gateway6,
            "mtu": network.mtu,
            "ipv4_forwarding": network.ipv4_forwarding,
            "ipv6_forwarding": network.ipv6_forwarding,
            "manage_routes": network.manage_routes,
            "manage_mtu": network.manage_mtu,
        }
        if network
        else None,
        "settings": app_settings,
        "excluded": ["users", "roles", "audit_logs", "system_metrics", "nat64_sessions", "auth_sessions"],
    }


def create_backup(
    db: Session,
    *,
    name: str,
    user: User | None = None,
    note: str | None = None,
    kind: str = "manual",
    is_automatic: bool = False,
) -> ConfigurationBackup:
    content = serialize_config(db)
    raw = json.dumps(content, sort_keys=True, default=str).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = backup_dir / f"{stamp}-{kind}.json"
    path.write_bytes(raw)
    record = ConfigurationBackup(
        name=name,
        kind=kind,
        content=content,
        checksum=checksum,
        size_bytes=len(raw),
        note=note,
        created_by=user.username if user else "system",
        is_automatic=is_automatic,
        file_path=str(path),
    )
    db.add(record)
    db.flush()
    write_audit(
        db,
        action="backup.create",
        user=user,
        resource_type="configuration_backup",
        resource_id=record.id,
        detail={"name": name, "kind": kind, "checksum": checksum, "automatic": is_automatic},
    )
    return record


def list_backups(db: Session) -> list[dict]:
    rows = db.scalars(select(ConfigurationBackup).order_by(ConfigurationBackup.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "checksum": row.checksum,
            "size_bytes": row.size_bytes,
            "note": row.note,
            "created_by": row.created_by,
            "is_automatic": row.is_automatic,
            "created_at": row.created_at,
            "file_path": row.file_path,
        }
        for row in rows
    ]


def validate_backup(content: dict) -> None:
    if not isinstance(content, dict):
        raise ValidationFailed("Backup content must be a JSON object")
    if content.get("app") != "nat64-isp-manager":
        raise ValidationFailed("Not a NAT64 ISP Manager backup")
    if int(content.get("schema", 0)) > BACKUP_SCHEMA:
        raise ValidationFailed(f"Backup schema {content.get('schema')} is newer than supported schema {BACKUP_SCHEMA}")
    for key in ("ipv4_pools", "ipv6_prefixes", "jool_instances"):
        if key in content and not isinstance(content[key], list):
            raise ValidationFailed(f"Backup field {key} must be a list")


def restore_backup(
    db: Session,
    backup: ConfigurationBackup,
    *,
    user: User | None = None,
    mode: str = "merge",
) -> dict:
    if mode not in {"merge", "replace"}:
        raise ValidationFailed("Restore mode must be merge or replace")
    if mode == "replace" and (not user or not user.is_superuser):
        raise ValidationFailed("Replace mode requires superuser")
    content = backup.content
    validate_backup(content)
    if mode == "replace":
        for address in db.scalars(select(IPv4Address)).all():
            db.delete(address)
        for pool in db.scalars(select(IPv4Pool)).all():
            db.delete(pool)
        for rule in db.scalars(select(FirewallRule)).all():
            db.delete(rule)
        for prefix in db.scalars(select(IPv6Prefix)).all():
            db.delete(prefix)
        db.flush()

    instance_by_name = {item.name: item for item in db.scalars(select(JoolInstance)).all()}
    for item in content.get("jool_instances", []):
        name = item.get("name")
        if not name:
            continue
        instance = instance_by_name.get(name)
        if not instance:
            instance = JoolInstance(name=name, pool6=item["pool6"], framework=item.get("framework", "netfilter"))
            db.add(instance)
            instance_by_name[name] = instance
        instance.pool6 = item.get("pool6", instance.pool6)
        instance.framework = item.get("framework", instance.framework)
        instance.enabled = item.get("enabled", True)
        instance.display_name = item.get("display_name")
        instance.namespace = item.get("namespace")
    db.flush()

    for item in content.get("ipv4_pools", []):
        pool = db.scalar(select(IPv4Pool).where(IPv4Pool.name == item.get("name")))
        if not pool:
            pool = IPv4Pool(name=item["name"])
            db.add(pool)
            db.flush()
        pool.description = item.get("description")
        pool.enabled = item.get("enabled", True)
        pool.color = item.get("color")
        instance_name = item.get("jool_instance")
        pool.jool_instance_id = instance_by_name[instance_name].id if instance_name in instance_by_name else None
        for address_item in item.get("addresses", []):
            existing = db.scalar(
                select(IPv4Address).where(
                    IPv4Address.pool_id == pool.id,
                    IPv4Address.address == address_item["address"],
                    IPv4Address.port_min == address_item["port_min"],
                    IPv4Address.port_max == address_item["port_max"],
                )
            )
            if not existing:
                db.add(
                    IPv4Address(
                        pool_id=pool.id,
                        address=address_item["address"],
                        port_min=address_item["port_min"],
                        port_max=address_item["port_max"],
                        enabled=address_item.get("enabled", True),
                        label=address_item.get("label"),
                        status="pending",
                    )
                )
            else:
                existing.enabled = address_item.get("enabled", existing.enabled)
                existing.label = address_item.get("label", existing.label)
                existing.status = "pending"
    for item in content.get("ipv6_prefixes", []):
        prefix = db.scalar(select(IPv6Prefix).where(IPv6Prefix.prefix == item.get("prefix")))
        if not prefix:
            prefix = IPv6Prefix(prefix=item["prefix"])
            db.add(prefix)
        prefix.name = item.get("name")
        prefix.interface = item.get("interface")
        prefix.vlan_id = item.get("vlan_id")
        prefix.is_client_prefix = item.get("is_client_prefix", True)
        prefix.enabled = item.get("enabled", True)
        prefix.description = item.get("description")
    dns = db.get(Dns64Settings, 1)
    if not dns:
        dns = Dns64Settings(id=1)
        db.add(dns)
    if content.get("dns64"):
        for key, value in content["dns64"].items():
            if hasattr(dns, key):
                setattr(dns, key, value)
        dns.config_version = (dns.config_version or 0) + 1
        dns.last_status = "pending"
    firewall = db.get(FirewallSettings, 1)
    if not firewall:
        firewall = FirewallSettings(id=1)
        db.add(firewall)
    fw = content.get("firewall") or {}
    if fw.get("settings"):
        for key, value in fw["settings"].items():
            if hasattr(firewall, key):
                setattr(firewall, key, value)
        firewall.config_version = (firewall.config_version or 0) + 1
    for item in fw.get("rules", []):
        rule = db.scalar(select(FirewallRule).where(FirewallRule.name == item.get("name")))
        if not rule:
            rule = FirewallRule(name=item["name"])
            db.add(rule)
        for key, value in item.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
    network = db.get(NetworkSettings, 1)
    if not network:
        network = NetworkSettings(id=1)
        db.add(network)
    if content.get("network"):
        for key, value in content["network"].items():
            if hasattr(network, key):
                setattr(network, key, value)
        network.config_version = (network.config_version or 0) + 1
    for item in content.get("settings", []):
        setting = db.get(Setting, item.get("key"))
        if not setting:
            setting = Setting(key=item["key"])
            db.add(setting)
        setting.value = item.get("value")
        setting.description = item.get("description")
        setting.updated_by = user.username if user else "restore"
    write_audit(
        db,
        action="backup.restore",
        user=user,
        resource_type="configuration_backup",
        resource_id=backup.id,
        detail={"mode": mode, "name": backup.name, "checksum": backup.checksum},
    )
    return {
        "restored": True,
        "mode": mode,
        "backup_id": backup.id,
        "note": "Control-plane configuration restored. Host configurations (Jool pool4, BIND, nftables, netplan) "
        "must be re-applied through approved change requests; this endpoint does not touch the host.",
    }
