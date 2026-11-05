from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FirewallSettings(Base, TimestampMixin):
    __tablename__ = "firewall_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_forward_policy: Mapped[str] = mapped_column(String(16), default="accept", nullable=False)
    default_input_policy: Mapped[str] = mapped_column(String(16), default="accept", nullable=False)
    new_connection_rate: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    rate_burst: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    log_dropped: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    management_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    management_ports: Mapped[list[int]] = mapped_column(JSON, default=lambda: [22, 443], nullable=False)
    protect_control_plane: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    syn_flood_protection: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    invalid_packet_protection: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mss_clamping: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_render_hash: Mapped[str | None] = mapped_column(String(64))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class FirewallRule(Base, TimestampMixin):
    __tablename__ = "firewall_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    family: Mapped[str] = mapped_column(String(8), default="inet", nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="forward", nullable=False)
    action: Mapped[str] = mapped_column(String(16), default="drop", nullable=False)
    protocol: Mapped[str | None] = mapped_column(String(16))
    source_cidr: Mapped[str | None] = mapped_column(String(64))
    destination_cidr: Mapped[str | None] = mapped_column(String(64))
    source_port: Mapped[int | None] = mapped_column(Integer)
    destination_port: Mapped[int | None] = mapped_column(Integer)
    rate_limit_per_second: Mapped[int | None] = mapped_column(Integer)
    log: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    counter_packets: Mapped[int | None] = mapped_column(Integer)
    counter_bytes: Mapped[int | None] = mapped_column(Integer)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
