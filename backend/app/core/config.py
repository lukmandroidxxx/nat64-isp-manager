"""Application settings.

All host integration settings are explicit. Unsafe features are opt-in.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "NAT64 ISP Manager"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # --- database / cache -------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'nat64.db').as_posix()}"
    database_echo: bool = False
    sqlite_fallback_allowed: bool = True
    redis_url: str | None = None
    cache_ttl_seconds: int = 5

    # --- security ---------------------------------------------------------
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    app_encryption_key: str | None = None
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    password_min_length: int = 12
    login_max_failures: int = 8
    login_lockout_minutes: int = 15
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    trusted_hosts: list[str] = ["*"]
    secure_cookies: bool = False
    csrf_enabled: bool = False
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 10

    # --- bootstrap --------------------------------------------------------
    default_admin_username: str = "admin"
    default_admin_password: str | None = None
    default_admin_email: str = "admin@example.invalid"
    bootstrap_admin: bool = True

    # --- host control -----------------------------------------------------
    host_control_mode: Literal["auto", "direct", "sudo", "disabled"] = "auto"
    hostctl_path: str = str(BACKEND_DIR.parent / "deploy" / "hostctl" / "hostctl.py")
    hostctl_python: str = "python3"
    hostctl_use_sudo: bool = True
    hostctl_timeout_seconds: int = 45
    allow_package_install: bool = False
    command_log_limit: int = 4000

    # --- Jool -------------------------------------------------------------
    jool_binary: str = "jool"
    default_jool_instance: str = "default"
    jool_netns: str | None = None
    jool_framework: Literal["netfilter"] = "netfilter"
    jool_min_version: str = "4.0.0"
    jool_command_timeout: int = 20
    jool_pool4_default_port_min: int = 61001
    jool_pool4_default_port_max: int = 65535

    # --- DNS64 / BIND -----------------------------------------------------
    dns_managed_config: str = "/etc/bind/nat64-manager.conf"
    dns_service_name: str = "named"
    dns_stats_path: str = "/var/cache/bind/named.stats"
    dns_check_binary: str = "named-checkconf"
    dns_control_binary: str = "rndc"
    dns_test_domain: str = "ipv4only.arpa"
    dns_default_upstreams: list[str] = ["1.1.1.1", "8.8.8.8"]
    dns_listen_address: str = "127.0.0.1"
    dns_query_timeout_seconds: int = 4

    # --- network ----------------------------------------------------------
    network_managed_netplan: str = "/etc/netplan/90-nat64-manager.yaml"
    network_managed_sysctl: str = "/etc/sysctl.d/90-nat64-manager.conf"
    network_backup_dir: str = "/var/backups/nat64-manager"
    network_rollback_seconds: int = 120
    firewall_managed_file: str = "/etc/nftables.d/nat64-manager.nft"
    firewall_table_name: str = "nat64mgr"
    backup_dir: str = str(BACKEND_DIR / "data" / "backups")

    # --- collector --------------------------------------------------------
    collector_enabled: bool = True
    collector_interval_seconds: int = 5
    session_sync_interval_seconds: int = 5
    metrics_interval_seconds: int = 10
    metrics_retention_days: int = 30
    session_retention_hours: int = 24
    collect_all_protocols: bool = True
    max_sessions_per_sync: int = 50000

    # --- high availability ------------------------------------------------
    ha_enabled: bool = False
    ha_node_name: str = "gateway-1"
    ha_role: Literal["primary", "backup", "standalone"] = "standalone"
    ha_sync_token: str | None = None
    ha_heartbeat_seconds: int = 10

    # --- frontend / deployment -------------------------------------------
    serve_frontend: bool = False
    frontend_dist: str = str(BACKEND_DIR.parent / "frontend" / "dist")
    log_level: str = "INFO"
    log_file: str | None = None
    timezone: str = "UTC"
    enable_docs: bool = True
    demo_mode: bool = False  # must remain False in production; never generates fake data

    @field_validator("cors_origins", "trusted_hosts", "dns_default_upstreams", mode="before")
    @classmethod
    def _split_csv(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("secret_key")
    @classmethod
    def _secret_strength(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def backend_dir(self) -> Path:
        return BACKEND_DIR


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
