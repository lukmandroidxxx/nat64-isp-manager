from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- auth -----------------------------------------------------------------


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class LogoutRequest(StrictModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UserCreate(StrictModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["viewer", "operator", "admin", "superadmin"]
    email: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class UserUpdate(StrictModel):
    email: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    role: Literal["viewer", "operator", "admin", "superadmin"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


# --- IPv4 pools -----------------------------------------------------------


class PoolCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool = True
    jool_instance_id: int | None = None
    color: str | None = Field(default=None, max_length=16)


class PoolUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
    jool_instance_id: int | None = None
    color: str | None = Field(default=None, max_length=16)


class AddressCreate(StrictModel):
    address: str = Field(min_length=7, max_length=43)
    port_min: int = Field(default=61001, ge=1, le=65535)
    port_max: int = Field(default=65535, ge=1, le=65535)
    enabled: bool = True
    label: str | None = Field(default=None, max_length=128)


class AddressUpdate(StrictModel):
    address: str | None = Field(default=None, min_length=7, max_length=43)
    port_min: int | None = Field(default=None, ge=1, le=65535)
    port_max: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=128)


class AddressImport(StrictModel):
    text: str = Field(min_length=1, max_length=200_000)
    port_min: int = Field(default=61001, ge=1, le=65535)
    port_max: int = Field(default=65535, ge=1, le=65535)
    enabled: bool = True


class PrefixCreate(StrictModel):
    prefix: str = Field(min_length=2, max_length=64)
    name: str | None = Field(default=None, max_length=128)
    interface: str | None = Field(default=None, max_length=64)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    is_client_prefix: bool = True
    enabled: bool = True
    description: str | None = Field(default=None, max_length=4000)


class PrefixUpdate(StrictModel):
    prefix: str | None = Field(default=None, min_length=2, max_length=64)
    name: str | None = Field(default=None, max_length=128)
    interface: str | None = Field(default=None, max_length=64)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    is_client_prefix: bool | None = None
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=4000)


class ClientUpdate(StrictModel):
    notes: str | None = Field(default=None, max_length=4000)
    hostname: str | None = Field(default=None, max_length=255)
    is_blocked: bool | None = None


# --- Jool / changes -------------------------------------------------------


class JoolInstanceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=15)
    pool6: str = Field(min_length=2, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)


class JoolInstanceUpdate(StrictModel):
    display_name: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None


class JoolRecreate(StrictModel):
    instance_id: int
    pool6: str = Field(min_length=2, max_length=64)


class JoolGlobalUpdate(StrictModel):
    instance_id: int
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=512)


class ChangeCreate(StrictModel):
    action: str = Field(min_length=3, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class ChangeReject(StrictModel):
    reason: str | None = Field(default=None, max_length=2000)


# --- DNS64 ----------------------------------------------------------------


class Dns64Update(StrictModel):
    enabled: bool | None = None
    prefix: str | None = Field(default=None, min_length=2, max_length=64)
    upstreams: list[str] | None = None
    listen_address: str | None = Field(default=None, max_length=64)
    service_name: Literal["named", "bind9"] | None = None
    managed_config_path: str | None = Field(default=None, max_length=255)
    test_domain: str | None = Field(default=None, max_length=255)
    exclude_prefixes: list[str] | None = None
    forward_only: bool | None = None
    dnssec_validation: bool | None = None


class Dns64Test(StrictModel):
    domain: str | None = Field(default=None, max_length=255)


# --- firewall -------------------------------------------------------------


class FirewallSettingsUpdate(StrictModel):
    enabled: bool | None = None
    default_forward_policy: Literal["accept", "drop"] | None = None
    default_input_policy: Literal["accept", "drop"] | None = None
    new_connection_rate: int | None = Field(default=None, ge=1, le=100_000)
    rate_burst: int | None = Field(default=None, ge=1, le=100_000)
    log_dropped: bool | None = None
    management_allowlist: list[str] | None = None
    management_ports: list[int] | None = None
    protect_control_plane: bool | None = None
    syn_flood_protection: bool | None = None
    invalid_packet_protection: bool | None = None
    mss_clamping: bool | None = None


class FirewallRuleCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool = True
    priority: int = Field(default=100, ge=1, le=100_000)
    family: Literal["inet", "ipv4", "ipv6"] = "inet"
    direction: Literal["input", "forward"] = "forward"
    action: Literal["accept", "drop", "reject", "log"] = "drop"
    protocol: Literal["tcp", "udp", "icmp", "icmpv6"] | None = None
    source_cidr: str | None = Field(default=None, max_length=64)
    destination_cidr: str | None = Field(default=None, max_length=64)
    source_port: int | None = Field(default=None, ge=1, le=65535)
    destination_port: int | None = Field(default=None, ge=1, le=65535)
    rate_limit_per_second: int | None = Field(default=None, ge=1, le=100_000)
    log: bool = False


class FirewallRuleUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=100_000)
    family: Literal["inet", "ipv4", "ipv6"] | None = None
    direction: Literal["input", "forward"] | None = None
    action: Literal["accept", "drop", "reject", "log"] | None = None
    protocol: Literal["tcp", "udp", "icmp", "icmpv6"] | None = None
    source_cidr: str | None = Field(default=None, max_length=64)
    destination_cidr: str | None = Field(default=None, max_length=64)
    source_port: int | None = Field(default=None, ge=1, le=65535)
    destination_port: int | None = Field(default=None, ge=1, le=65535)
    rate_limit_per_second: int | None = Field(default=None, ge=1, le=100_000)
    log: bool | None = None


# --- network --------------------------------------------------------------


class NetworkInterfaceUpdate(StrictModel):
    role: Literal["wan4", "wan6", "lan6", "lan4", "management", "other"] | None = None
    description: str | None = Field(default=None, max_length=255)


class NetworkSettingsUpdate(StrictModel):
    wan4_interface: str | None = Field(default=None, max_length=64)
    wan6_interface: str | None = Field(default=None, max_length=64)
    lan6_interface: str | None = Field(default=None, max_length=64)
    client_prefix: str | None = Field(default=None, max_length=64)
    nat64_prefix: str | None = Field(default=None, max_length=64)
    dns_server: str | None = Field(default=None, max_length=64)
    default_gateway4: str | None = Field(default=None, max_length=45)
    default_gateway6: str | None = Field(default=None, max_length=64)
    mtu: int | None = Field(default=None, ge=576, le=9216)
    ipv4_forwarding: bool | None = None
    ipv6_forwarding: bool | None = None
    manage_routes: bool | None = None
    manage_mtu: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


# --- backup ---------------------------------------------------------------


class BackupCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=4000)


class BackupRestore(StrictModel):
    mode: Literal["merge", "replace"] = "merge"


# --- HA -------------------------------------------------------------------


class HaSettingsUpdate(StrictModel):
    enabled: bool | None = None
    mode: Literal["keepalived", "manual"] | None = None
    virtual_ip: str | None = Field(default=None, max_length=64)
    vrrp_interface: str | None = Field(default=None, max_length=64)
    vrrp_router_id: int | None = Field(default=None, ge=1, le=255)
    priority: int | None = Field(default=None, ge=1, le=254)
    peer_address: str | None = Field(default=None, max_length=64)
    auth_secret: str | None = Field(default=None, min_length=8, max_length=128)
    session_sync_enabled: bool | None = None
    session_sync_multicast: str | None = Field(default=None, max_length=64)
    session_sync_interface: str | None = Field(default=None, max_length=64)


class HaNodeCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    role: Literal["primary", "backup", "standalone"] = "backup"
    address: str | None = Field(default=None, max_length=255)
    api_url: str | None = Field(default=None, max_length=255)
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=4000)


class HaNodeUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    role: Literal["primary", "backup", "standalone"] | None = None
    address: str | None = Field(default=None, max_length=255)
    api_url: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


class HaSyncImport(StrictModel):
    config: dict[str, Any]
    config_hash: str | None = Field(default=None, max_length=128)


# --- wizard ---------------------------------------------------------------


class WizardStep(StrictModel):
    step: int = Field(ge=1, le=11)
    data: dict[str, Any] = Field(default_factory=dict)


# --- settings -------------------------------------------------------------


class SettingUpdate(StrictModel):
    value: Any
    description: str | None = Field(default=None, max_length=4000)
