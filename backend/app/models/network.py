from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BigIntPK, TimestampMixin, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from app.models.jool import Nat64Session


class IPv4Pool(Base, TimestampMixin):
    __tablename__ = "ipv4_pools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    jool_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("jool_instances.id", ondelete="SET NULL"), index=True
    )
    color: Mapped[str | None] = mapped_column(String(16))

    addresses: Mapped[list[IPv4Address]] = relationship(
        back_populates="pool", cascade="all, delete-orphan", lazy="selectin"
    )
    jool_instance = relationship("JoolInstance", back_populates="pools")


class IPv4Address(Base, TimestampMixin):
    __tablename__ = "ipv4_addresses"
    __table_args__ = (
        UniqueConstraint("pool_id", "address", "port_min", "port_max", name="uq_ipv4_address_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("ipv4_pools.id", ondelete="CASCADE"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(43), nullable=False, index=True)
    port_min: Mapped[int] = mapped_column(Integer, default=61001, nullable=False)
    port_max: Mapped[int] = mapped_column(Integer, default=65535, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_session_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pool: Mapped[IPv4Pool] = relationship(back_populates="addresses")
    sessions: Mapped[list[Nat64Session]] = relationship(back_populates="pool4_address")


class IPv6Prefix(Base, TimestampMixin):
    __tablename__ = "ipv6_prefixes"

    id: Mapped[int] = mapped_column(primary_key=True)
    prefix: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    interface: Mapped[str | None] = mapped_column(String(64))
    vlan_id: Mapped[int | None] = mapped_column(Integer)
    is_client_prefix: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class IPv6Client(Base, TimestampMixin):
    """An observed IPv6 address, not a durable subscriber identity.

    Privacy extensions can rotate the address. MAC is populated only from the
    kernel neighbor table when available.
    """

    __tablename__ = "ipv6_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    prefix_id: Mapped[int | None] = mapped_column(ForeignKey("ipv6_prefixes.id", ondelete="SET NULL"))
    mac_address: Mapped[str | None] = mapped_column(String(32))
    interface: Mapped[str | None] = mapped_column(String(64))
    vlan_id: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    active_session_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class NetworkInterface(Base, TimestampMixin):
    __tablename__ = "network_interfaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(32))
    mtu: Mapped[int | None] = mapped_column(Integer)
    speed_mbps: Mapped[int | None] = mapped_column(Integer)
    is_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vlan_id: Mapped[int | None] = mapped_column(Integer)
    addresses: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class NetworkSettings(Base, TimestampMixin):
    __tablename__ = "network_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    wan4_interface: Mapped[str | None] = mapped_column(String(64))
    wan6_interface: Mapped[str | None] = mapped_column(String(64))
    lan6_interface: Mapped[str | None] = mapped_column(String(64))
    client_prefix: Mapped[str | None] = mapped_column(String(64))
    nat64_prefix: Mapped[str] = mapped_column(String(64), default="64:ff9b::/96", nullable=False)
    dns_server: Mapped[str | None] = mapped_column(String(64))
    default_gateway4: Mapped[str | None] = mapped_column(String(45))
    default_gateway6: Mapped[str | None] = mapped_column(String(64))
    mtu: Mapped[int] = mapped_column(Integer, default=1500, nullable=False)
    ipv4_forwarding: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ipv6_forwarding: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manage_routes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manage_mtu: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollout_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_render_hash: Mapped[str | None] = mapped_column(String(64))
    last_snapshot: Mapped[str | None] = mapped_column(String(512))
    pending_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class TrafficSample(Base):
    """Optional high-volume flow samples (not required by the dashboard)."""

    __tablename__ = "traffic_samples"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    client_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    bytes_up: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_down: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
