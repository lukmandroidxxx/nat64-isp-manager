from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntPK, TimestampMixin, utcnow


class SystemMetric(Base):
    __tablename__ = "system_metrics"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    memory_used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    load1: Mapped[float | None] = mapped_column(Float)
    net_rx_bps: Mapped[float | None] = mapped_column(Float)
    net_tx_bps: Mapped[float | None] = mapped_column(Float)
    clients_total: Mapped[int | None] = mapped_column(Integer)
    sessions_total: Mapped[int | None] = mapped_column(Integer)
    tcp_sessions: Mapped[int | None] = mapped_column(Integer)
    udp_sessions: Mapped[int | None] = mapped_column(Integer)
    icmp_sessions: Mapped[int | None] = mapped_column(Integer)
    translations_per_second: Mapped[float | None] = mapped_column(Float)
    packet_translation_rate: Mapped[float | None] = mapped_column(Float)
    pool_utilization_percent: Mapped[float | None] = mapped_column(Float)
    jool_online: Mapped[bool | None] = mapped_column(Boolean)
    dns_online: Mapped[bool | None] = mapped_column(Boolean)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ConfigurationBackup(Base, TimestampMixin):
    __tablename__ = "configuration_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))
    is_automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512))


class ChangeRequest(Base, TimestampMixin):
    __tablename__ = "change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(String(512))
    risk: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    rejected_by: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class InstallationState(Base, TimestampMixin):
    __tablename__ = "installation_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    current_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_check: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class HaNode(Base, TimestampMixin):
    __tablename__ = "ha_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="backup", nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    api_url: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    jool_version: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)


class HaSettings(Base, TimestampMixin):
    __tablename__ = "ha_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="keepalived", nullable=False)
    virtual_ip: Mapped[str | None] = mapped_column(String(64))
    vrrp_interface: Mapped[str | None] = mapped_column(String(64))
    vrrp_router_id: Mapped[int] = mapped_column(Integer, default=51, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    peer_address: Mapped[str | None] = mapped_column(String(64))
    auth_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    session_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_sync_multicast: Mapped[str | None] = mapped_column(String(64))
    session_sync_interface: Mapped[str | None] = mapped_column(String(64))
    last_failover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
